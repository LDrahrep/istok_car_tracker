"""Тесты импорта людей, объектов и присутствия.

Все случаи взяты из реальных данных таблицы, а не придуманы: перестановка
имени и фамилии, написание капсом, опечатки в именах листов, шапки с чужой
недели.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from importer import (  # noqa: E402
    PresenceRow,
    latest_site_by_person,
    name_key,
    parse_timesheet,
    site_from_sheet_name,
    split_shift_and_site,
    week_dates_from_name,
)

BUFFALO_SHEET = "09142026-09202026 BUFFALO"


# ───────────────────────────── имена ─────────────────────────────

def test_name_key_handles_swapped_order():
    """В week2 «Khushmamadov Khushmamad», в списке ID — наоборот."""
    assert name_key("Khushmamadov Khushmamad") == name_key("Khushmamad Khushmamadov")


def test_name_key_ignores_case():
    """9 человек записаны капсом, остальные — Title Case."""
    assert name_key("BOBO RAHMONZODA") == name_key("Bobo Rahmonzoda")


def test_name_key_collapses_whitespace_and_invisibles():
    assert name_key("  Ivan   Ivanov ") == "ivan ivanov"
    assert name_key("Ivan Ivanov") == "ivan ivanov"


def test_name_key_keeps_different_people_apart():
    """Azimbek и Azizbek — РАЗНЫЕ люди, схлопывать их нельзя."""
    assert name_key("Azimbek Abdyrakhmanov") != name_key("Azizbek Abdyrakhmanov")
    assert name_key("Baskho Batukaev") != name_key("Surkho Batukaev")


# ──────────────────────── смена отдельно от объекта ────────────────────────

def test_meltech_splits_into_shift_and_site():
    assert split_shift_and_site("Meltech Day") == ("day", "MELTECH")
    assert split_shift_and_site("Meltech Night") == ("night", "MELTECH")


def test_bare_meltech_means_day():
    """Историческое значение: голый «Meltech» = дневная смена."""
    assert split_shift_and_site("Meltech") == ("day", "MELTECH")


def test_plain_shifts_have_no_site():
    assert split_shift_and_site("Day") == ("day", None)
    assert split_shift_and_site("night") == ("night", None)
    assert split_shift_and_site("") == ("unknown", None)


# ──────────────────────────── листы-табели ────────────────────────────

def test_site_recognised_from_sheet_suffix():
    assert site_from_sheet_name(BUFFALO_SHEET) == "BUFFALO"
    assert site_from_sheet_name("8242026-8302026 AMAZON") == "AMAZON"


def test_known_typos_still_resolve():
    """MELTEH и MILTECH реально встречались в именах листов."""
    assert site_from_sheet_name("0907-0913 MELTEH") == "MELTECH"
    assert site_from_sheet_name("0907-0913 MILTECH") == "MELTECH"


def test_svodka_sheets_are_not_timesheets():
    """«Svodka Columbus» заканчивается на название объекта, но это вывод."""
    assert site_from_sheet_name("Svodka Columbus") is None
    assert site_from_sheet_name("employees") is None


def test_week_dates_from_sheet_name():
    dates = week_dates_from_name(BUFFALO_SHEET)
    assert len(dates) == 7
    assert dates[0] == date(2026, 9, 14)
    assert dates[-1] == date(2026, 9, 20)


def test_week_dates_accept_slashes():
    """В живой таблице имена бывают со слэшами."""
    assert week_dates_from_name("8/17/2026-8/23/2026 AMAZON")[0] == date(2026, 8, 17)


def test_week_dates_absent_without_pattern():
    assert week_dates_from_name("PHASE 5 AMAZON") is None


# ──────────────────────────── разбор табеля ────────────────────────────

HEADER = ["", "", "09/14/2026", "09/15/2026", "09/16/2026", "", "", "", ""]
WEEKDAYS = ["", "Name", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def test_parse_timesheet_reads_marks():
    values = [HEADER, WEEKDAYS,
              ["1", "Badma Matsakov", "", "8.0", "10.0", "", "", "", ""],
              ["2", "Sergei Olimov", "11.0", "", "", "", "", "", ""]]
    rows = parse_timesheet(values, BUFFALO_SHEET)
    assert len(rows) == 3
    assert {r.site_id for r in rows} == {"BUFFALO"}
    # Пустая ячейка — не присутствие.
    assert sum(1 for r in rows if r.name == "Badma Matsakov") == 2


def test_parse_timesheet_skips_second_row_and_empty_names():
    values = [HEADER, WEEKDAYS, ["", "", "8.0", "", "", "", "", "", ""]]
    assert parse_timesheet(values, BUFFALO_SHEET) == []


def test_parse_timesheet_ignores_non_timesheet_sheet():
    values = [HEADER, WEEKDAYS, ["1", "Ivan", "8.0", "", "", "", "", "", ""]]
    assert parse_timesheet(values, "Svodka Columbus") == []


def test_sheet_name_dates_win_over_header():
    """Шапки копируют с чужой недели — имя листа достовернее."""
    stale = ["", "", "01/01/2020", "", "", "", "", "", ""]
    values = [stale, WEEKDAYS, ["1", "Ivan", "8.0", "", "", "", "", "", ""]]
    rows = parse_timesheet(values, BUFFALO_SHEET)
    assert rows[0].work_date == date(2026, 9, 14)


# ─────────────────────── текущий объект из присутствия ───────────────────────

def test_current_site_follows_latest_mark():
    """Переезд между объектами виден из табеля, вручную вести не нужно."""
    rows = [PresenceRow("Ivan Ivanov", date(2026, 9, 20), "AMAZON"),
            PresenceRow("Ivan Ivanov", date(2026, 10, 1), "BUFFALO")]
    assert latest_site_by_person(rows)[name_key("Ivan Ivanov")] == "BUFFALO"


def test_current_site_matches_people_across_spellings():
    rows = [PresenceRow("BOBO RAHMONZODA", date(2026, 10, 1), "AMAZON")]
    assert latest_site_by_person(rows)[name_key("Bobo Rahmonzoda")] == "AMAZON"
