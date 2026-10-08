"""Чтение справочников из Postgres вместо Google Sheets.

Этап 2 перехода. Снимает основную нагрузку на квоту: сейчас каждое
нажатие кнопки читает лист `employees` на полторы тысячи строк, а квота
около 60 запросов в минуту почти выбирается еженедельной рассылкой.

Включается переменной USE_DB_READS. По умолчанию выключено: откат — одна
переменная окружения, без деплоя.

Условие, при котором это безопасно, проверено 08.10 измерением: смена
всех 1517 сотрудников совпадает в листе и в базе при разборе ровно тем
же `ShiftType.from_string`, которым пользуется бот. Поддерживается
ежедневной синхронизацией ростера в 20:45 — без неё база отстаёт
и читать из неё нельзя.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Короткий кэш: за одно действие пользователя справочник спрашивают
# несколько раз (проверка водителя, поиск пассажира, подсказки).
_CACHE_TTL = 5.0
_cache: dict[str, tuple[float, list]] = {}

EMPLOYEES_SQL = """
SELECT p.full_name,
       p.shift,
       COALESCE(d.full_name, '') AS rides_with,
       d.telegram_id
FROM person p
LEFT JOIN carpool c ON c.passenger_id = p.id AND c.ride_date = %s
LEFT JOIN person d ON d.id = c.driver_id
WHERE p.is_active
ORDER BY p.full_name
"""


def enabled() -> bool:
    return os.getenv("USE_DB_READS", "").strip().lower() in ("1", "true", "yes", "да")


def employees() -> Optional[list]:
    """Справочник сотрудников из базы в том же виде, что отдаёт лист.

    Возвращает None при любой неудаче — вызывающий откатывается на Sheets.
    Пустой список тоже считается неудачей: справочник не может быть пуст,
    а значит это сбой, а не факт.
    """
    from models import Employee

    from .capture import today_in_business_tz
    from .store import _connect, enabled as db_enabled

    if not db_enabled():
        return None

    hit = _cache.get("employees")
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(EMPLOYEES_SQL, (today_in_business_tz(),))
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — откат на Sheets важнее
        logger.error("Чтение справочника из БД не удалось: %s", exc)
        return None

    if not rows:
        logger.error("Справочник из БД пуст — откат на Sheets")
        return None

    out = [
        Employee(name=full_name, phone="", shift=shift or "",
                 rides_with=rides_with or "",
                 tg_id=int(tg) if tg is not None else None)
        for full_name, shift, rides_with, tg in rows
    ]
    _cache["employees"] = (time.time(), out)
    return out


def invalidate() -> None:
    """Сбросить кэш после записи, чтобы следующее чтение увидело изменение."""
    _cache.pop("employees", None)


WHOIS_SQL = """
SELECT p.id, p.full_name, p.shift, p.telegram_id, p.current_site_id,
       (SELECT max(pr.work_date) FROM presence pr WHERE pr.person_id = p.id),
       (SELECT count(*) FROM presence pr WHERE pr.person_id = p.id)
FROM person p
WHERE p.name_key = %s OR p.telegram_id = %s
LIMIT 1
"""

# Кого он возит сегодня и с кем едет сам — два разных вопроса,
# потому что один и тот же человек бывает и водителем, и пассажиром.
DRIVES_SQL = """
SELECT pass.full_name FROM carpool c JOIN person pass ON pass.id = c.passenger_id
WHERE c.driver_id = %s AND c.ride_date = %s ORDER BY c.seat
"""
RIDES_SQL = """
SELECT d.full_name, d.telegram_id FROM carpool c JOIN person d ON d.id = c.driver_id
WHERE c.passenger_id = %s AND c.ride_date = %s
"""
HISTORY_SQL = """
SELECT c.ride_date, d.full_name FROM carpool c JOIN person d ON d.id = c.driver_id
WHERE c.passenger_id = %s ORDER BY c.ride_date DESC LIMIT 7
"""


def whois(query: str) -> Optional[dict]:
    """Всё, что база знает о человеке: по имени или по telegram_id."""
    from .capture import today_in_business_tz
    from .importer import name_key
    from .store import _connect, enabled as db_enabled

    if not db_enabled():
        return None
    q = (query or "").strip()
    if not q:
        return None
    tg = int(q) if q.isdigit() else -1
    today = today_in_business_tz()

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(WHOIS_SQL, (name_key(q), tg))
        row = cur.fetchone()
        if not row:
            return {"found": False, "query": q}
        pid, full_name, shift, tgid, site, last_seen, days = row
        cur.execute(DRIVES_SQL, (pid, today))
        drives = [r[0] for r in cur.fetchall()]
        cur.execute(RIDES_SQL, (pid, today))
        rides = cur.fetchone()
        cur.execute(HISTORY_SQL, (pid,))
        history = cur.fetchall()

    return {"found": True, "name": full_name, "shift": shift, "telegram_id": tgid,
            "site": site, "last_seen": last_seen, "presence_days": days,
            "drives": drives, "rides_with": rides, "history": history}
