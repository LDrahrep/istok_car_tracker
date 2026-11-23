"""Правки к выплатам: разбор листа «правки» (ВОДИЛЫ) и сравнение с таблицей payout_fix.

Здесь только чистая логика, без Google и базы: чтение листа и запись в базу
делает скрипт переноса поверх этих функций. Правка узнаётся между переносами
по тройке (ключ имени, период, текст дней); среди одинаковых троек — сперва по
номеру строки (sheet_row), остальные по порядку.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from db.importer import name_key

VODILY_ID = "1YAXRP2hHJq-TLIsCA8Ovhk9R65FXgIFogc6nfIrbB4A"
FIXES_TAB = "правки"

_PERIOD = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:\s*[-–—]\s*(\d{1,2})/(\d{1,2}))?\s*$")
_WINDOW_DAYS = 183


def is_green(color: dict) -> bool:
    """Заливка зелёная: зелёный канал заметно выше красного и синего (#00ff00 и светлые оттенки)."""
    r = color.get("red", 0) or 0
    g = color.get("green", 0) or 0
    b = color.get("blue", 0) or 0
    return g - max(r, b) >= 0.05


def parse_period(text: str, near: date) -> tuple[date | None, date | None]:
    """«MM/DD» или «MM/DD-MM/DD» в даты; год начала — ближайший к near (не дальше 183 дней)."""
    m = _PERIOD.match(text or "")
    if not m:
        return None, None
    m1, d1 = int(m.group(1)), int(m.group(2))
    best = None
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            start = date(year, m1, d1)
        except ValueError:
            continue  # например 02/29 в невисокосном году: смотрим другой год
        gap = abs((start - near).days)
        if gap <= _WINDOW_DAYS and (best is None or gap < best[0]):
            best = (gap, start)
    if best is None:
        return None, None
    start = best[1]
    if m.group(3) is None:
        return start, start
    try:
        end = date(start.year, int(m.group(3)), int(m.group(4)))
        if end < start:
            end = date(start.year + 1, int(m.group(3)), int(m.group(4)))
    except ValueError:
        return None, None
    return start, end


def parse_days(text: str) -> int | None:
    """Первое целое число в тексте «14 дней»."""
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


@dataclass(frozen=True)
class SheetRow:
    """Строка листа «правки»: row — номер в листе с 1, green — заливка колонки A."""
    row: int
    name: str
    period: str
    days_text: str
    green: bool

    @property
    def key(self) -> str:
        return name_key(self.name)


def rows_from_sheet(values: list[list[str]], colors: list[dict]) -> list[SheetRow]:
    """Строки A:C (с первой, шапки нет) и фон колонки A той же длины; пустое имя пропускается."""
    out = []
    for i, vals in enumerate(values):
        cells = [str(v).strip() for v in vals] + [""] * 3
        if not cells[0]:
            continue
        color = colors[i] if i < len(colors) else {}
        out.append(SheetRow(i + 1, cells[0], cells[1], cells[2], is_green(color or {})))
    return out


@dataclass(frozen=True)
class DbFix:
    """Запись payout_fix в том виде, что нужна для сравнения.

    driver_name None — имя неизвестно, с листом не сверяется.
    """
    id: int
    driver_key: str
    period: str
    days_text: str
    sheet_row: int | None
    paid: bool
    removed: bool
    driver_name: str | None = None
    added_at: datetime | None = None
    paid_at: datetime | None = None


@dataclass
class ImportPlan:
    new: list[SheetRow] = field(default_factory=list)
    changed: list[tuple[DbFix, SheetRow]] = field(default_factory=list)
    paid: list[tuple[DbFix, SheetRow]] = field(default_factory=list)
    unpaid: list[tuple[DbFix, SheetRow]] = field(default_factory=list)
    removed: list[DbFix] = field(default_factory=list)
    returned: list[tuple[DbFix, SheetRow]] = field(default_factory=list)
    seen: list[tuple[DbFix, SheetRow]] = field(default_factory=list)
    # узнанные (seen/returned), у которых имя в листе написано не так, как в базе
    renamed: list[tuple[DbFix, SheetRow]] = field(default_factory=list)

    def empty(self) -> bool:
        """Менять нечего: допускается только seen (узнанные без изменений)."""
        return not (self.new or self.changed or self.paid or self.unpaid
                    or self.removed or self.returned or self.renamed)


def _match_paid(plan: ImportPlan, fix: DbFix, row: SheetRow) -> None:
    if row.green and not fix.paid:
        plan.paid.append((fix, row))
    elif fix.paid and not row.green:
        plan.unpaid.append((fix, row))


def _pair_triples(sheet: list[SheetRow], db: list[DbFix]) -> dict[int, DbFix]:
    """Номер строки листа → запись базы с той же тройкой.

    Внутри группы одинаковых троек запись сперва берёт строку со своим
    sheet_row (на один номер — сперва действующая), остальные по порядку:
    k-я свободная строка листа ↔ k-я свободная запись по id. Иначе две
    одинаковые строки, стоящие не в порядке id, менялись бы оплатой.
    """
    rows_by_triple: dict[tuple, list[SheetRow]] = {}
    for r in sheet:
        rows_by_triple.setdefault((r.key, r.period, r.days_text), []).append(r)
    db_by_triple: dict[tuple, list[DbFix]] = {}
    for f in sorted(db, key=lambda f: (f.removed, f.id)):
        db_by_triple.setdefault((f.driver_key, f.period, f.days_text), []).append(f)
    pair: dict[int, DbFix] = {}
    for t, rows in rows_by_triple.items():
        free = {r.row for r in rows}
        rest = []
        for f in db_by_triple.get(t, []):
            if f.sheet_row in free:
                pair[f.sheet_row] = f
                free.discard(f.sheet_row)
            else:
                rest.append(f)
        rest.sort(key=lambda f: f.id)
        for r, f in zip([r for r in rows if r.row in free], rest):
            pair[r.row] = f
    return pair


def plan_import(sheet: list[SheetRow], db: list[DbFix]) -> ImportPlan:
    plan = ImportPlan()
    # 1. Точные тройки (см. _pair_triples).
    pair = _pair_triples(sheet, db)
    used: set[int] = set()
    rest_rows: list[SheetRow] = []
    for r in sheet:
        f = pair.get(r.row)
        if f is None:
            rest_rows.append(r)
            continue
        used.add(f.id)
        if f.driver_name is not None and f.driver_name != r.name:
            plan.renamed.append((f, r))  # регистр, пробелы, порядок слов — как в листе
        if f.removed:
            plan.returned.append((f, r))
            _match_paid(plan, f, r)  # оплата сверяется с заливкой в этом же переносе
        else:
            plan.seen.append((f, r))
            _match_paid(plan, f, r)
    # 2. Исправление на месте: тот же номер строки, отличается ровно одно поле.
    rest_db = [f for f in db if f.id not in used and not f.removed]
    by_row = {f.sheet_row: f for f in rest_db if f.sheet_row is not None}
    taken: set[int] = set()
    for r in rest_rows:
        f = by_row.get(r.row)
        if f is not None and f.id not in taken:
            diff = ((f.driver_key != r.key) + (f.period != r.period)
                    + (f.days_text != r.days_text))
            if diff == 1:
                taken.add(f.id)
                plan.changed.append((f, r))
                _match_paid(plan, f, r)
                continue
        plan.new.append(r)
    # 3. Остальные действующие записи базы сняты.
    plan.removed = [f for f in rest_db if f.id not in taken]
    return plan
