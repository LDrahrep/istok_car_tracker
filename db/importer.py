"""Импорт людей, объектов и присутствия из Google Sheets в Postgres.

Направление одностороннее: Sheets → БД, только чтение. Это соответствует
правилу «у каждой сущности ровно один владелец»: справочник сотрудников и
табели пока ведёт HR в таблице, поэтому БД их импортирует и не трогает.

Почему объект берётся из присутствия, а не из отдельной колонки: колонку
пришлось бы заполнять руками на каждого человека и поддерживать при каждом
переезде. Табели и так заполняются — значит объект уже known, его надо
только прочитать.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

# Суффикс в имени листа-табеля → канонический объект. Опечатки реальны:
# в таблице встречались и MELTEH, и MILTECH.
SITE_ALIASES = {
    "AMAZON": "AMAZON", "AMZN": "AMAZON",
    "MELTECH": "MELTECH", "MELTEH": "MELTECH", "MILTECH": "MELTECH", "MLT": "MELTECH",
    "COLUMBUS": "COLUMBUS", "CLMB": "COLUMBUS", "CBUS": "COLUMBUS",
    "BUFFALO": "BUFFALO", "BUFF": "BUFFALO", "BUF": "BUFFALO", "BFLO": "BUFFALO",
}
SITE_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(sorted(SITE_ALIASES, key=len, reverse=True)) + r")\s*$", re.I
)
WEEK_RANGE_RE = re.compile(
    r"(\d{1,2})\D?(\d{1,2})\D?(\d{4})\s*-\s*\d{1,2}\D?\d{1,2}\D?\d{4}"
)

# Листы, которые заканчиваются на название объекта, но табелями не являются.
NOT_TIMESHEETS = {"drivers", "drivers_passengers", "employees",
                  "svodka columbus", "svodka buffalo", "svodka amazon"}


@dataclass
class PresenceRow:
    name: str
    work_date: date
    site_id: str


def name_key(raw: str) -> str:
    """Ключ сопоставления: отсортированные токены в нижнем регистре.

    Берёт на себя главную беду этих данных — перестановку имени и фамилии
    («Khushmamadov Khushmamad» против «Khushmamad Khushmamadov») и разницу
    регистра (9 человек записаны капсом). NFKC убирает невидимые символы,
    которых в выгрузках хватает.
    """
    s = unicodedata.normalize("NFKC", raw or "")
    s = s.replace(" ", " ").replace("​", "").replace("﻿", "")
    tokens = [t for t in s.casefold().split() if t]
    return " ".join(sorted(tokens))


def split_shift_and_site(raw_shift: str) -> tuple[str, Optional[str]]:
    """«Meltech Day» → ('day', 'MELTECH'), «Night» → ('night', None).

    Объект сидел внутри значения смены — из-за этого Meltech был случайно
    отделён от Amazon, а Columbus и Buffalo не были отделены ничем.
    Здесь это разводится на два независимых поля.
    """
    s = (raw_shift or "").replace(" ", " ").strip().casefold()
    if not s:
        return "unknown", None
    site = None
    if "meltech" in s or "meltch" in s:
        site = "MELTECH"
        s = s.replace("meltech", "").replace("meltch", "").strip()
    if "night" in s:
        return "night", site
    if "day" in s:
        return "day", site
    # Голый «Meltech» исторически означал дневную смену.
    return ("day", site) if site else ("unknown", None)


def site_from_sheet_name(sheet_name: str) -> Optional[str]:
    name = (sheet_name or "").strip()
    if name.casefold() in NOT_TIMESHEETS:
        return None
    m = SITE_SUFFIX_RE.search(name)
    return SITE_ALIASES[m.group(1).upper()] if m else None


def week_dates_from_name(sheet_name: str) -> Optional[list[date]]:
    """Семь дат из имени листа вида 09142026-09202026.

    Имя приоритетнее строки 1: шапки часто копируют с чужой недели, и тогда
    даты в них врут. Вторая дата диапазона игнорируется — она служит только
    якорем шаблона.
    """
    m = WEEK_RANGE_RE.search(str(sheet_name or ""))
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        start = date(yy, mm, dd)
    except ValueError:
        return None
    return [start + timedelta(days=i) for i in range(7)]


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(value or "").strip())
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def parse_timesheet(values: Sequence[Sequence[object]], sheet_name: str) -> list[PresenceRow]:
    """Табель → список фактов присутствия.

    Геометрия жёсткая и такая же, как в GAS: даты — строка 1, колонки C..I;
    имена — колонка B; данные начинаются со строки 3; присутствием считается
    любая непустая ячейка (часы, крестик, инициалы — значение не разбирается).
    """
    site = site_from_sheet_name(sheet_name)
    if not site or len(values) < 3:
        return []

    dates = week_dates_from_name(sheet_name)
    if dates is None:
        header = values[0]
        dates = [_as_date(header[2 + i]) if 2 + i < len(header) else None
                 for i in range(7)]
    if not any(dates):
        return []

    out: list[PresenceRow] = []
    for row in values[2:]:
        name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        if not name:
            continue
        for i in range(7):
            day = dates[i] if i < len(dates) else None
            cell = row[2 + i] if 2 + i < len(row) else None
            if day and cell not in ("", None):
                out.append(PresenceRow(name=name, work_date=day, site_id=site))
    return out


def latest_site_by_person(rows: Iterable[PresenceRow]) -> dict[str, str]:
    """Текущий объект = объект самой свежей отметки.

    Отсюда и берётся current_site_id: вести его руками не нужно, переезд
    человека виден из табеля на следующий же день.
    """
    best: dict[str, tuple[date, str]] = {}
    for r in rows:
        key = name_key(r.name)
        prev = best.get(key)
        if prev is None or r.work_date > prev[0]:
            best[key] = (r.work_date, r.site_id)
    return {k: v[1] for k, v in best.items()}
