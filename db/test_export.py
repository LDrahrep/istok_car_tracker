"""Тесты выгрузки в Google Sheets.

Главное здесь — защита от промаха именем: выгрузка стирает лист перед
записью, и ошибка в названии означала бы стёртый `employees` с полутора
тысячами сотрудников. Поэтому проверка префикса отдельным тестом.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from export import (  # noqa: E402
    EXPORTS,
    UnsafeSheetName,
    cell,
    check_target,
    pad_passengers,
    snapshot_row,
    table,
)


# ───────────────────── защита целевого листа ─────────────────────

@pytest.mark.parametrize("title", ["employees", "drivers", "drivers_passengers",
                                   "week2", "Svodka Columbus", "db_snapshots"])
def test_refuses_to_overwrite_real_sheets(title):
    with pytest.raises(UnsafeSheetName):
        check_target(title)


def test_allows_own_sheets():
    assert check_target("_db_snapshots") == "_db_snapshots"


def test_every_declared_export_targets_a_safe_sheet():
    for e in EXPORTS:
        assert check_target(e.title) == e.title


# ───────────────────── формат ячеек ─────────────────────

def test_telegram_id_stays_a_string():
    """Числом Sheets покажет 1.23457E+9 — точность теряется безвозвратно."""
    assert cell(8077848816) == "8077848816"


def test_dates_are_plain_iso():
    assert cell(date(2026, 10, 5)) == "2026-10-05"
    assert cell(datetime(2026, 10, 5, 21, 5)) == "2026-10-05 21:05"


def test_booleans_are_readable():
    assert cell(True) == "да" and cell(False) == "нет"


def test_empty_cell_is_empty_string():
    assert cell(None) == ""


# ───────────────────── раскладка как в листе ─────────────────────

def test_passengers_always_four_columns():
    """Passenger1..4 — фиксированная геометрия исходного листа."""
    assert pad_passengers(["A", "B"]) == ["A", "B", "", ""]
    assert pad_passengers([]) == ["", "", "", ""]
    assert pad_passengers(None) == ["", "", "", ""]


def test_extra_passengers_are_cut_not_shifted():
    """Пятый не должен сдвинуть колонки и разъехать таблицу."""
    assert pad_passengers(["A", "B", "C", "D", "E"]) == ["A", "B", "C", "D"]


def test_snapshot_row_matches_drivers_passengers_layout():
    row = (date(2026, 10, 5), 8077848816, "Ivan Ivanov", "+1 408 555 0101",
           "Day", "AMAZON", ["Petr Petrov", "Semen Semenov"])
    assert snapshot_row(row) == [
        "2026-10-05", "Ivan Ivanov", "8077848816", "+1 408 555 0101",
        "Day", "AMAZON", "Petr Petrov", "Semen Semenov", "", "",
    ]


def test_table_puts_headers_first():
    values = table(("A", "B"), [(1, 2), (3, 4)])
    assert values[0] == ["A", "B"]
    assert values[1] == ["1", "2"]


def test_every_export_header_count_matches_its_rows():
    """Заголовок короче строки — данные уедут в соседнюю колонку."""
    row = (date(2026, 10, 5), 1, "N", "", "", "", ["a"])
    snapshots = next(e for e in EXPORTS if e.title == "_db_snapshots")
    assert len(snapshot_row(row)) == len(snapshots.headers)
