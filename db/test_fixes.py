"""Перенос правок из листа «правки»: разбор и сравнение с базой без Google и БД."""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.fixes import (  # noqa: E402
    DbFix, SheetRow, is_green, parse_days, parse_period, plan_import, rows_from_sheet,
)

GREEN = {"green": 1}
WHITE = {"red": 1, "green": 1, "blue": 1}


def sr(row, name="Ivan Testov", period="10/31", days="1 день", green=False):
    return SheetRow(row, name, period, days, green)


def dbf(id, name="Ivan Testov", period="10/31", days="1 день", row=None,
        paid=False, removed=False):
    return DbFix(id, SheetRow(0, name, period, days, False).key, period, days, row, paid, removed)


def test_green_is_the_sheets_green_and_its_light_shades():
    assert is_green({"green": 1})
    assert is_green({"red": 0.851, "green": 0.918, "blue": 0.827})
    assert is_green({"red": 0.714, "green": 0.843, "blue": 0.659})
    for other in ({"red": 1, "green": 1, "blue": 1}, {}, {"red": 1, "green": 0.949, "blue": 0.8},
                  {"red": 0.812, "green": 0.886, "blue": 0.953}, {"red": 0.851, "green": 0.851, "blue": 0.851}):
        assert not is_green(other)


def test_a_period_takes_the_year_nearest_to_the_reference():
    near = date(2026, 11, 22)
    assert parse_period("10/26-11/08", near) == (date(2026, 10, 26), date(2026, 11, 8))
    assert parse_period("10/31", near) == (date(2026, 10, 31), date(2026, 10, 31))
    assert parse_period("12/28-01/03", date(2027, 1, 10)) == (date(2026, 12, 28), date(2027, 1, 3))
    assert parse_period("на неделе", near) == (None, None)


def test_days_are_the_number_in_the_text():
    assert parse_days("14 дней") == 14 and parse_days("1 день") == 1 and parse_days("3 дня") == 3
    assert parse_days("") is None and parse_days("неделя") is None


def test_rows_skip_the_empty_and_keep_their_sheet_numbers():
    values = [["Ivan Testov", "10/31", "1 день"], ["", "", ""], [], ["Petr Probnyi", "10/18", "1 день"]]
    colors = [GREEN, WHITE, {}, WHITE]
    rows = rows_from_sheet(values, colors)
    assert [(r.row, r.name, r.green) for r in rows] == [(1, "Ivan Testov", True), (4, "Petr Probnyi", False)]
    assert rows[0].period == "10/31" and rows[0].days_text == "1 день"
    assert rows[0].key == sr(9, "Testov Ivan").key


def test_a_new_row_is_new_and_a_green_one_is_also_paid_later():
    plan = plan_import([sr(1), sr(2, "Petr Probnyi", green=True)], [])
    assert [r.row for r in plan.new] == [1, 2]
    assert not plan.empty()
    # зелёная строка уже в базе, но не оплачена: paid, без new
    plan = plan_import([sr(1, green=True)], [dbf(7, row=1)])
    assert [(f.id, r.row) for f, r in plan.paid] == [(7, 1)]
    assert plan.new == [] and [f.id for f, _ in plan.seen] == [7]


def test_a_rerun_without_changes_plans_nothing():
    sheet = [sr(1, green=True), sr(2, "Petr Probnyi")]
    db = [dbf(1, row=1, paid=True), dbf(2, "Petr Probnyi", row=2)]
    plan = plan_import(sheet, db)
    assert plan.empty()
    assert [f.id for f, _ in plan.seen] == [1, 2]


def test_two_identical_rows_match_two_records_in_order():
    sheet = [sr(1), sr(2), sr(3)]
    db = [dbf(10, row=1), dbf(11, row=2)]
    plan = plan_import(sheet, db)
    assert [(f.id, r.row) for f, r in plan.seen] == [(10, 1), (11, 2)]
    assert [r.row for r in plan.new] == [3]
    assert plan.removed == []


def test_a_row_fixed_in_place_is_changed_not_removed_and_new():
    plan = plan_import([sr(5, "Ivan Testov", "10/31", "2 дня")], [dbf(3, row=5)])
    assert [(f.id, r.row) for f, r in plan.changed] == [(3, 5)]
    assert plan.new == [] and plan.removed == []


def test_two_fields_changed_is_removed_and_new():
    plan = plan_import([sr(5, "Ivan Testov", "11/01", "2 дня")], [dbf(3, row=5)])
    assert plan.changed == []
    assert [f.id for f in plan.removed] == [3] and [r.row for r in plan.new] == [5]


def test_green_taken_off_is_unpaid_on_its_own():
    plan = plan_import([sr(1)], [dbf(4, row=1, paid=True)])
    assert [(f.id, r.row) for f, r in plan.unpaid] == [(4, 1)]
    assert plan.paid == [] and plan.new == [] and plan.removed == []


def test_a_row_gone_is_removed_and_its_return_is_returned():
    plan = plan_import([], [dbf(1, row=1), dbf(2, "Petr Probnyi", row=2, removed=True)])
    assert [f.id for f in plan.removed] == [1]      # снятая уже снята: повторно не трогаем
    plan = plan_import([sr(2, "Petr Probnyi")], [dbf(2, "Petr Probnyi", row=9, removed=True)])
    assert [(f.id, r.row) for f, r in plan.returned] == [(2, 2)]
    assert plan.new == [] and plan.removed == [] and plan.seen == []


def test_a_returned_green_row_on_an_unpaid_record_is_also_paid():
    plan = plan_import([sr(2, green=True)], [dbf(2, row=9, removed=True, paid=False)])
    assert [f.id for f, _ in plan.returned] == [2]
    assert [f.id for f, _ in plan.paid] == [2] and plan.unpaid == []


def test_a_returned_white_row_on_a_paid_record_is_also_unpaid():
    plan = plan_import([sr(2)], [dbf(2, row=9, removed=True, paid=True)])
    assert [f.id for f, _ in plan.returned] == [2]
    assert [f.id for f, _ in plan.unpaid] == [2] and plan.paid == []


def test_the_period_accepts_en_and_em_dashes_spaces_and_feb_29():
    near = date(2026, 11, 22)
    want = (date(2026, 10, 26), date(2026, 11, 8))
    for t in ("10/26–11/08", "10/26—11/08", "10/26 - 11/08", "10/26 – 11/08"):
        assert parse_period(t, near) == want
    assert parse_period("02/29", date(2028, 3, 1)) == (date(2028, 2, 29), date(2028, 2, 29))
