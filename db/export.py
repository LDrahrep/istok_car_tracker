"""Выгрузка из Postgres обратно в Google Sheets.

Зачем, если данные и так пришли оттуда: в БД они нормализованы и сведены —
история за все недели в одном месте, конфликты и люди вне ростера посчитаны.
В таблице этого не было, а разбираться с ними удобнее там, где человек и так
работает, а не в SQL-клиенте.

Раскладка повторяет `drivers_passengers`: Name, telegramID, Shift,
Passenger1..4 — чтобы глаз не переучивался.

Направление по-прежнему одностороннее и правило «один владелец у сущности»
не нарушается: пишем ТОЛЬКО в свои листы с префиксом `_db_`, которых не
читает ни бот, ни GAS. Ни одна существующая сущность второго писателя
не получает.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional, Sequence

# Префикс обязателен: выгрузка стирает лист перед записью, и промах именем
# означал бы стёртый `employees`. Проверяется до любого обращения к Google.
SHEET_PREFIX = "_db_"

MAX_PASSENGERS = 4


class UnsafeSheetName(Exception):
    """Попытка записать выгрузку в чужой лист."""


def check_target(title: str) -> str:
    if not title.startswith(SHEET_PREFIX):
        raise UnsafeSheetName(
            f"выгрузка пишет только в листы «{SHEET_PREFIX}*», получено «{title}»"
        )
    return title


def cell(value) -> str:
    """Ячейка в виде, который Sheets не испортит.

    Telegram ID отдаём строкой намеренно: числом Sheets показывает его как
    1.23457E+9 и точность теряется безвозвратно — на этом уже спотыкался
    разбор снапшотов.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def pad_passengers(passengers: Optional[Sequence[str]]) -> list[str]:
    """Массив → ровно четыре колонки, как Passenger1..4 в листе."""
    items = list(passengers or [])[:MAX_PASSENGERS]
    return items + [""] * (MAX_PASSENGERS - len(items))


def snapshot_row(row: Sequence) -> list[str]:
    """Строка carpool_snapshot → строка в раскладке drivers_passengers."""
    snapshot_date, tg, name, phone, shift, site, passengers = row[:7]
    return [cell(snapshot_date), cell(name), cell(tg), cell(phone),
            cell(shift), cell(site)] + pad_passengers(passengers)


def table(headers: Sequence[str], rows, formatter: Callable = None) -> list[list[str]]:
    """Заголовок плюс строки — ровно то, что принимает Sheets."""
    out = [list(headers)]
    for row in rows:
        out.append(formatter(row) if formatter else [cell(v) for v in row])
    return out


@dataclass(frozen=True)
class Export:
    title: str
    sql: str
    headers: tuple[str, ...]
    formatter: Optional[Callable] = None


EXPORTS: tuple[Export, ...] = (
    Export(
        title="_db_snapshots",
        sql="SELECT snapshot_date, telegram_id, driver_name, phone, shift_raw,"
            " site_raw, passengers FROM carpool_snapshot"
            " ORDER BY snapshot_date DESC, driver_name",
        headers=("Date", "Name", "telegramID", "Phone Number", "Shift", "Site",
                 "Passenger1", "Passenger2", "Passenger3", "Passenger4"),
        formatter=snapshot_row,
    ),
    Export(
        title="_db_conflicts",
        sql="SELECT дата, написания, водители FROM v_conflicts ORDER BY дата DESC",
        headers=("Date", "Написания имени", "Водители"),
    ),
    Export(
        title="_db_unknown",
        sql="SELECT имя, дней, с, по FROM v_unknown_passengers ORDER BY дней DESC",
        headers=("Имя", "Дней", "С", "По"),
    ),
    Export(
        title="_db_credits",
        sql="SELECT дата, водитель, объект, пассажиров, отмечен_в_табеле,"
            " день_засчитан FROM v_credit_day ORDER BY дата DESC, водитель",
        headers=("Date", "Водитель", "Объект", "Пассажиров",
                 "Отмечен в табеле", "День засчитан"),
    ),
)


def export(sheets, only: Optional[str] = None) -> dict:
    """Пишет выгрузки в таблицу. Возвращает статистику по листам."""
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}

    targets = [e for e in EXPORTS
               if only is None or only.casefold() in e.title.casefold()]
    for e in targets:
        check_target(e.title)

    written: dict[str, int] = {}
    with _connect() as conn:
        for e in targets:
            with conn.cursor() as cur:
                cur.execute(e.sql)
                values = table(e.headers, cur.fetchall(), e.formatter)
            sheets.replace_sheet(e.title, values)
            written[e.title] = len(values) - 1
    return {"enabled": True, "sheets": written}
