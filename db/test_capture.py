"""Тесты разбора снапшотов. Без сети и без базы — только чистые функции."""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import (  # noqa: E402
    col_map,
    dedupe_last_wins,
    parse_sheet,
    parse_tgid,
    SnapshotRow,
)

LIVE_HEADER = ["Name", "telegramID", "Phone Number", "Shift",
               "Passenger1", "Passenger2", "Passenger3", "Passenger4", "site"]
WEEK_HEADER = LIVE_HEADER + ["Passenger note", "SnapshotKey", "SnapshotDateTime"]


def test_col_map_ignores_case_and_spaces():
    cols = col_map(["  Name ", "telegramID", "Phone Number"])
    assert cols["name"] == 0
    assert cols["telegramid"] == 1
    assert cols["phone number"] == 2


def test_parse_tgid_accepts_plain_and_float_suffix():
    assert parse_tgid("1900931880") == 1900931880
    assert parse_tgid(" 1900931880 ") == 1900931880
    # Sheets часто отдаёт целое как "123.0"
    assert parse_tgid("1900931880.0") == 1900931880


def test_parse_tgid_rejects_lossy_scientific_notation():
    # Точность уже потеряна — чинить наугад нельзя, строка должна отпасть.
    assert parse_tgid("1.23457E+9") is None
    assert parse_tgid("") is None
    assert parse_tgid("abc") is None


def test_live_sheet_parsed_with_fixed_date():
    values = [
        LIVE_HEADER,
        ["Aldar Rakshaev", "1900931880", "", "Night",
         "Sandzhi Komushev", "Ruslan Shapiev", "", "", "AMAZON"],
    ]
    res = parse_sheet(values, fixed_date=date(2026, 10, 5))
    assert res.read == 1
    assert res.skipped == 0
    row = res.rows[0]
    assert row.snapshot_date == date(2026, 10, 5)
    assert row.telegram_id == 1900931880
    assert row.driver_name == "Aldar Rakshaev"
    assert row.shift_raw == "Night"
    assert row.site_raw == "AMAZON"
    assert row.passengers == ["Sandzhi Komushev", "Ruslan Shapiev"]


def test_passenger_gaps_collapse():
    # В листе дырки между Passenger1..4 — в массив попадают только непустые.
    values = [LIVE_HEADER,
              ["Ivan", "11", "", "Day", "", "Petr", "", "Maria", ""]]
    res = parse_sheet(values, fixed_date=date(2026, 10, 5))
    assert res.rows[0].passengers == ["Petr", "Maria"]


def test_week_sheet_takes_date_from_snapshot_key():
    values = [
        WEEK_HEADER,
        ["Ivan", "11", "", "Day", "A", "B", "", "", "",
         "2 and more passengers", "SK|2026-09-21|21:00", "2026-09-21 21:54:43"],
    ]
    res = parse_sheet(values)
    assert res.rows[0].snapshot_date == date(2026, 9, 21)
    assert res.rows[0].passengers == ["A", "B"]


def test_week_row_without_snapshot_key_is_skipped():
    values = [WEEK_HEADER, ["Ivan", "11", "", "Day", "A", "B", "", "", "", "", "", ""]]
    res = parse_sheet(values)
    assert res.rows == []
    assert res.skipped == 1
    assert "нет SnapshotKey" in res.reasons


def test_missing_required_columns_is_reported_not_crashed():
    res = parse_sheet([["Что-то", "Другое"], ["a", "b"]])
    assert res.rows == []
    assert res.skipped == 1


def test_blank_rows_are_not_counted():
    values = [LIVE_HEADER, ["", "", "", "", "", "", "", "", ""]]
    res = parse_sheet(values, fixed_date=date(2026, 10, 5))
    assert res.read == 0
    assert res.rows == []


def test_dedupe_keeps_last_row_per_driver_and_date():
    # Ровно случай week2: два прогона append за один день (21:54 и 23:19).
    early = SnapshotRow(date(2026, 9, 21), 11, "Ivan", passengers=["A"])
    late = SnapshotRow(date(2026, 9, 21), 11, "Ivan", passengers=["A", "B"])
    other_day = SnapshotRow(date(2026, 9, 22), 11, "Ivan", passengers=["C"])

    result = dedupe_last_wins([early, late, other_day])

    assert len(result) == 2
    same_day = [r for r in result if r.snapshot_date == date(2026, 9, 21)]
    assert same_day[0].passengers == ["A", "B"], "должна выигрывать поздняя строка"


def test_dedupe_does_not_merge_different_drivers():
    rows = [
        SnapshotRow(date(2026, 9, 21), 11, "Ivan", passengers=["A"]),
        SnapshotRow(date(2026, 9, 21), 22, "Petr", passengers=["B"]),
    ]
    assert len(dedupe_last_wins(rows)) == 2


def test_real_week2_duplication_shape():
    """244 строки при 122 водителях схлопываются ровно в 122."""
    rows = []
    for run in range(2):
        for tgid in range(1, 123):
            rows.append(SnapshotRow(date(2026, 9, 21), tgid, f"D{tgid}",
                                    passengers=[f"P{tgid}", f"Q{run}"]))
    assert len(rows) == 244
    deduped = dedupe_last_wins(rows)
    assert len(deduped) == 122
    assert all(r.passengers[1] == "Q1" for r in deduped), "выигрывает второй прогон"
