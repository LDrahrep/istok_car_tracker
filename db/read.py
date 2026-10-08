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
