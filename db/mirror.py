"""Зеркалирование карпулов в Postgres в момент изменения.

Этап 1 перехода: источник истины по-прежнему Google Sheets, но каждое
изменение сразу повторяется в БД. Это даёт две вещи. Во-первых, картина в
базе перестаёт отставать на сутки — раньше изменения появлялись только
в снимке в 21:05. Во-вторых, накапливается материал для сверки: прежде чем
переключать чтение на базу, надо убедиться, что обе картины совпадают.

Любая ошибка здесь проглатывается. На этом этапе запись в Sheets уже
состоялась, и уронить ответ пользователю из-за недоступной базы значило бы
променять работающий сценарий на зеркало. Все отказы попадают в лог.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Человек, которого нет в ростере, заводится на лету — так же, как это
# делает импорт. Пропускать его нельзя: именно такие строки и теряются
# незаметно, а потом обнаруживаются как «пассажир не найден».
ENSURE_PERSON = """
INSERT INTO person (full_name, name_key, shift)
VALUES (%s, %s, 'unknown')
ON CONFLICT (name_key) DO UPDATE SET updated_at = now()
RETURNING id
"""

CLEAR_DAY = "DELETE FROM carpool WHERE ride_date = %s AND driver_id = %s"

INSERT_LINK = """
INSERT INTO carpool (ride_date, driver_id, passenger_id, seat, source)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


def _person_id(cur, full_name: str) -> Optional[int]:
    from .importer import name_key

    key = name_key(full_name)
    if not key:
        return None
    cur.execute(ENSURE_PERSON, (full_name.strip(), key))
    row = cur.fetchone()
    return row[0] if row else None


def sync_carpool(tg_id: int, driver_name: str, passengers: Iterable[str],
                 *, day: Optional[date] = None, actor: str = "bot",
                 source: str = "bot") -> dict:
    """Повторить в БД текущий состав карпула водителя на сегодня.

    Состояние переписывается целиком, а не дописывается: список в боте —
    это именно состояние, и «было двое, стал один» должно выглядеть так же
    и в базе. Поэтому день сначала очищается от строк этого водителя.
    """
    from .capture import today_in_business_tz
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}

    passengers = [p.strip() for p in (passengers or []) if p and p.strip()]
    day = day or today_in_business_tz()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM person WHERE telegram_id = %s", (tg_id,)
                )
                row = cur.fetchone()
                driver_id = row[0] if row else _person_id(cur, driver_name)
                if driver_id is None:
                    return {"enabled": True, "written": 0, "reason": "no_driver"}
                # Привязываем telegram_id к записи, если его там ещё нет —
                # иначе следующий вызов снова пойдёт искать по имени.
                cur.execute(
                    "UPDATE person SET telegram_id = %s, updated_at = now()"
                    " WHERE id = %s AND telegram_id IS NULL", (tg_id, driver_id),
                )

                cur.execute(CLEAR_DAY, (day, driver_id))
                written, skipped = 0, []
                for seat, name in enumerate(passengers[:4], start=1):
                    pid = _person_id(cur, name)
                    if pid is None or pid == driver_id:
                        skipped.append(name)
                        continue
                    cur.execute(INSERT_LINK, (day, driver_id, pid, seat, source))
                    written += cur.rowcount
                    if cur.rowcount == 0:
                        # Пассажира уже везёт кто-то другой в этот день:
                        # первичный ключ (дата, пассажир) не даст записать.
                        skipped.append(name)
            conn.commit()
        if skipped:
            logger.info("mirror: %s — не записаны %s", driver_name, skipped)
        return {"enabled": True, "written": written, "skipped": skipped,
                "day": day, "actor": actor}
    except Exception as exc:  # noqa: BLE001 — зеркало не должно ронять бота
        logger.error("mirror: не удалось отразить карпул %s: %s", driver_name, exc)
        return {"enabled": True, "error": str(exc)}


def diff_states(sheet: dict[int, list[str]],
                db: dict[int, list[str]]) -> dict[str, list]:
    """Сравнить состав карпулов в таблице и в базе.

    Смысл этапа 1 — накопить доказательство, что обе картины совпадают,
    прежде чем переключать чтение на базу. Без такой сверки переключение
    было бы прыжком в темноте.

    Сравнение по нормализованному имени: в таблице «Akmal Shah», в базе
    может лежать «Shah Akmal» — это один человек, а не расхождение.
    """
    from .importer import name_key

    norm = lambda names: {name_key(n) for n in names if n and n.strip()}
    out: dict[str, list] = {"only_sheet": [], "only_db": [], "differs": []}

    for tg in set(sheet) | set(db):
        s, d = sheet.get(tg), db.get(tg)
        if d is None and s:
            out["only_sheet"].append((tg, s))
        elif s is None and d:
            out["only_db"].append((tg, d))
        elif s is not None and d is not None and norm(s) != norm(d):
            out["differs"].append((tg, s, d))
    return out


def compare(sheets, sheet_name: str, *, day: Optional[date] = None) -> dict:
    """Текущее состояние таблицы против состояния базы на тот же день."""
    from .capture import parse_sheet, today_in_business_tz
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}

    day = day or today_in_business_tz()
    parsed = parse_sheet(sheets._values(sheet_name), fixed_date=day)
    sheet_state = {r.telegram_id: list(r.passengers) for r in parsed.rows}

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT d.telegram_id, p.full_name FROM carpool c"
            " JOIN person d ON d.id = c.driver_id"
            " JOIN person p ON p.id = c.passenger_id"
            " WHERE c.ride_date = %s AND d.telegram_id IS NOT NULL",
            (day,),
        )
        db_state: dict[int, list[str]] = {}
        for tg, passenger in cur.fetchall():
            db_state.setdefault(int(tg), []).append(passenger)

    result = diff_states(sheet_state, db_state)
    result.update({"enabled": True, "day": day,
                   "sheet_drivers": len(sheet_state), "db_drivers": len(db_state)})
    return result


def rebuild_day(day: Optional[date] = None) -> dict:
    """Пересобрать связи за день из снимка.

    Нужно потому, что снимок и связи — разные слои: `/db_capture` пишет
    в `carpool_snapshot`, а сверка и сводки смотрят в `carpool`. Без этого
    шага свежий снимок есть, а связей за день нет, и сверка показывает
    пустую базу при полной таблице.

    Переиспользует тот же путь, что и зеркало: один код — одно поведение.
    """
    from .capture import today_in_business_tz
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}
    day = day or today_in_business_tz()

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT telegram_id, driver_name, passengers FROM carpool_snapshot"
            " WHERE snapshot_date = %s", (day,),
        )
        rows = cur.fetchall()

    written = 0
    for tg_id, driver_name, passengers in rows:
        res = sync_carpool(int(tg_id), driver_name or "", passengers or [],
                           day=day, source="snapshot")
        written += res.get("written", 0)
    return {"enabled": True, "day": day, "drivers": len(rows), "written": written}
