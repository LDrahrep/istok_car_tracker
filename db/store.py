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
from datetime import date
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
RETURNING (xmax = 0) AS inserted
"""


def dsn() -> Optional[str]:
    value = os.getenv("DATABASE_URL", "").strip()
    return value or None


def enabled() -> bool:
    return dsn() is not None


def _connect():
    import psycopg

    return psycopg.connect(dsn())


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
    inserted = updated = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(UPSERT_SQL, (
                row.snapshot_date, row.telegram_id, row.driver_name, row.phone,
                row.shift_raw, row.site_raw, row.passengers, source,
            ))
            if cur.fetchone()[0]:
                inserted += 1
            else:
                updated += 1
    return inserted, updated


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
    return {
        "enabled": True, "rows": rows, "drivers": drivers,
        "first": first, "last": last, "days": days, "runs": runs,
    }
