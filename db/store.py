"""Фасад Postgres для бота.

Вся работа с БД идёт ВНУТРИ Railway, где `postgres.railway.internal` резолвится.
Снаружи этот адрес недоступен, поэтому схема накатывается при старте бота, а
backfill запускается админской командой — публичная сеть для Postgres не нужна.

Если `DATABASE_URL` не задан, модуль целиком выключается и бот работает ровно
как раньше. Это сознательно: захват — параллельная подстраховка, он не должен
уметь уронить основной сценарий.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .capture import SnapshotRow, dedupe_last_wins, parse_sheet, today_in_business_tz

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

UPSERT_SQL = """
INSERT INTO carpool_snapshot (
    snapshot_date, telegram_id, driver_name, phone,
    shift_raw, site_raw, passengers, source
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (telegram_id, snapshot_date) DO UPDATE SET
    driver_name   = EXCLUDED.driver_name,
    phone         = EXCLUDED.phone,
    shift_raw     = EXCLUDED.shift_raw,
    site_raw      = EXCLUDED.site_raw,
    passengers    = EXCLUDED.passengers,
    source        = EXCLUDED.source,
    captured_at   = now(),
    capture_count = carpool_snapshot.capture_count + 1
"""

# Сколько из переносимых пар уже лежит в базе. Нужно, чтобы отличить
# «вставлено» от «обновлено» одним запросом вместо RETURNING на каждой строке.
COUNT_EXISTING_SQL = """
SELECT count(*) FROM carpool_snapshot
WHERE (telegram_id, snapshot_date) IN (
    SELECT * FROM unnest(%s::bigint[], %s::date[])
)
"""


def dsn() -> Optional[str]:
    value = os.getenv("DATABASE_URL", "").strip()
    return value or None


def enabled() -> bool:
    return dsn() is not None


def _connect():
    """Соединение с обязательным таймаутом.

    Без connect_timeout psycopg ждёт бесконечно и молча: команда «висит»,
    не отдавая ни ошибки, ни результата. Лучше честно упасть за 10 секунд.
    """
    import psycopg

    return psycopg.connect(dsn(), connect_timeout=10)


def apply_schema() -> bool:
    """Накатывает schema.sql. Идемпотентна — вся схема на CREATE IF NOT EXISTS."""
    if not enabled():
        logger.info("DATABASE_URL не задан — захват снапшотов выключен")
        return False
    try:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        logger.info("Схема БД применена")
        return True
    except Exception as exc:  # noqa: BLE001 — БД не должна ронять бота
        logger.error("Не удалось применить схему БД: %s", exc)
        return False


def _write(conn, rows: list[SnapshotRow], source: str) -> tuple[int, int]:
    """Пишет пачкой, возвращает (вставлено, обновлено).

    Раньше тут был RETURNING на каждой строке — 1600 отдельных round-trip'ов
    на один week-лист, то есть десятки секунд. Теперь два запроса: посчитать
    уже существующие пары и отправить весь пакет одним executemany.
    """
    if not rows:
        return 0, 0

    params = [
        (r.snapshot_date, r.telegram_id, r.driver_name, r.phone,
         r.shift_raw, r.site_raw, r.passengers, source)
        for r in rows
    ]
    with conn.cursor() as cur:
        cur.execute(COUNT_EXISTING_SQL, (
            [r.telegram_id for r in rows],
            [r.snapshot_date for r in rows],
        ))
        already = cur.fetchone()[0]
        cur.executemany(UPSERT_SQL, params)
    return len(rows) - already, already


def capture(sheets, sheet_name: str, *, source: str,
            fixed_date: Optional[date] = None) -> dict:
    """Читает лист и пишет снапшоты. Возвращает статистику прогона.

    `sheets` — уже созданный SheetManager бота: переиспользуем его retry и кэш
    вместо второго подключения к Google.
    """
    if not enabled():
        return {"enabled": False}

    values = sheets._values(sheet_name)
    parsed = parse_sheet(values, fixed_date=fixed_date)
    rows = dedupe_last_wins(parsed.rows)
    collapsed = len(parsed.rows) - len(rows)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO capture_run (source, rows_read) VALUES (%s, %s) RETURNING id",
                (source, parsed.read),
            )
            run_id = cur.fetchone()[0]
        try:
            inserted, updated = _write(conn, rows, source)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE capture_run SET finished_at = now(), error = %s WHERE id = %s",
                    (str(exc)[:2000], run_id),
                )
            conn.commit()
            raise
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE capture_run SET finished_at = now(), rows_written = %s,"
                " rows_updated = %s, rows_skipped = %s WHERE id = %s",
                (inserted, updated, parsed.skipped, run_id),
            )
        conn.commit()

    return {
        "enabled": True, "sheet": sheet_name, "source": source,
        "read": parsed.read, "inserted": inserted, "updated": updated,
        "skipped": parsed.skipped, "collapsed": collapsed,
        "reasons": parsed.reasons,
    }


def capture_live(sheets, sheet_name: str) -> dict:
    return capture(sheets, sheet_name, source="live",
                   fixed_date=today_in_business_tz())


def backfill(sheets, week_sheet: str) -> dict:
    return capture(sheets, week_sheet, source=f"backfill:{week_sheet}")


def status() -> dict:
    """Сводка состояния захвата: объём, глубина истории, последние прогоны."""
    if not enabled():
        return {"enabled": False}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT telegram_id),"
            " min(snapshot_date), max(snapshot_date),"
            " count(DISTINCT snapshot_date) FROM carpool_snapshot"
        )
        rows, drivers, first, last, days = cur.fetchone()
        cur.execute(
            "SELECT source, started_at, rows_written, rows_updated,"
            " rows_skipped, error FROM capture_run"
            " ORDER BY started_at DESC LIMIT 5"
        )
        runs = cur.fetchall()
        cur.execute(
            "SELECT snapshot_date, count(*) FROM carpool_snapshot"
            " GROUP BY snapshot_date ORDER BY snapshot_date"
        )
        per_day = cur.fetchall()

    # Пропуски важнее общего числа дней: «22 дня» звучит хорошо, но если
    # внутри дыра в неделю, то за эту неделю доплаты посчитать нечем.
    gaps: list[date] = []
    if first and last:
        have = {d for d, _ in per_day}
        cursor = first
        while cursor <= last:
            if cursor not in have:
                gaps.append(cursor)
            cursor += timedelta(days=1)

    return {
        "enabled": True, "rows": rows, "drivers": drivers,
        "first": first, "last": last, "days": days, "runs": runs,
        "per_day": per_day, "gaps": gaps,
    }


def log_event(actor: str, action: str, subject: str, details: dict) -> bool:
    """Записать событие в журнал изменений.

    Никогда не бросает и не блокирует вызывающего: журнал — страховка, а не
    условие работы. Если БД недоступна, очистка списка всё равно должна
    произойти, иначе еженедельная проверка зависнет на недоступной базе.
    Цена отказа — потерянная запись в журнале, и об этом пишется в лог.
    """
    if not enabled():
        return False
    try:
        import json

        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log (actor, action, subject, details)"
                    " VALUES (%s, %s, %s, %s::jsonb)",
                    (actor, action, subject, json.dumps(details, ensure_ascii=False)),
                )
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — журнал не должен ронять бота
        logger.error("Не удалось записать в журнал (%s/%s): %s", actor, action, exc)
        return False


def cleared_carpools(since_days: int = 30) -> list[tuple]:
    """Списки, стёртые еженедельной проверкой, — для восстановления."""
    if not enabled():
        return []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT happened_at, subject, details FROM audit_log"
            " WHERE action = 'weekly_clear'"
            "   AND happened_at > now() - make_interval(days => %s)"
            " ORDER BY happened_at DESC",
            (since_days,),
        )
        return cur.fetchall()
