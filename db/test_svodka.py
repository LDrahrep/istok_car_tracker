"""Тесты сводки по доплатам."""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from svodka import pivot, sheet_title, week_label, weeks_in  # noqa: E402


def test_week_label_matches_the_existing_svodka():
    """«09/14 - 09/20» — формат нынешнего листа, его нельзя менять."""
    assert week_label(date(2026, 9, 14)) == "09/14 - 09/20"


def test_weeks_cover_a_period_that_starts_midweek():
    """Период могут задать с любой даты — неделя всё равно с понедельника."""
    weeks = weeks_in(date(2026, 9, 16), date(2026, 9, 29))
    assert weeks == [date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]


def test_single_week_gives_one_column():
    assert weeks_in(date(2026, 9, 14), date(2026, 9, 20)) == [date(2026, 9, 14)]


def test_table_repeats_the_existing_layout():
    rows = [("TULANE", "Ivan Ivanov", date(2026, 9, 14), 7)]
    table = pivot(rows, weeks_in(date(2026, 9, 14), date(2026, 9, 20)))["TULANE"]

    assert table[0] == ["Водитель", "09/14 - 09/20",
                        "Возил дней", "В табеле", "Часов", "Комментарий"]
    assert table[1] == ["Ivan Ivanov", "7", "", "", "", ""]


def test_two_weeks_give_two_columns_like_the_biweekly_report():
    rows = [("TULANE", "Ivan Ivanov", date(2026, 9, 14), 7),
            ("TULANE", "Ivan Ivanov", date(2026, 9, 21), 5)]
    weeks = weeks_in(date(2026, 9, 14), date(2026, 9, 27))
    table = pivot(rows, weeks)["TULANE"]

    assert table[0] == ["Водитель", "09/14 - 09/20", "09/21 - 09/27",
                        "Возил дней", "В табеле", "Часов", "Комментарий"]
    assert table[1] == ["Ivan Ivanov", "7", "5", "", "", "", ""]


def test_missing_week_leaves_the_cell_empty_not_zero():
    """Пусто и «0» читаются по-разному: пусто — не работал, 0 — не засчитано."""
    rows = [("TULANE", "Ivan Ivanov", date(2026, 9, 21), 5)]
    table = pivot(rows, weeks_in(date(2026, 9, 14), date(2026, 9, 27)))["TULANE"]
    assert table[1] == ["Ivan Ivanov", "", "5", "", "", "", ""]


def test_each_site_gets_its_own_table():
    """Решение гриля №5: один объект — одна сводка."""
    rows = [("TULANE", "A A", date(2026, 9, 14), 7),
            ("BUFFALO", "B B", date(2026, 9, 14), 4)]
    tables = pivot(rows, weeks_in(date(2026, 9, 14), date(2026, 9, 20)))

    assert set(tables) == {"TULANE", "BUFFALO"}
    assert tables["BUFFALO"][1] == ["B B", "4", "", "", "", ""]


def test_drivers_sorted_by_name():
    rows = [("TULANE", "Yan Yanov", date(2026, 9, 14), 7),
            ("TULANE", "Anna Annova", date(2026, 9, 14), 7)]
    table = pivot(rows, weeks_in(date(2026, 9, 14), date(2026, 9, 20)))["TULANE"]
    assert [r[0] for r in table[1:]] == ["Anna Annova", "Yan Yanov"]


def test_sheet_title_is_inside_the_safe_prefix():
    """Выгрузка стирает лист перед записью — имя обязано пройти check_target."""
    from export import check_target
    assert check_target(sheet_title("TULANE")) == "_db_svodka_tulane"


def test_comment_column_is_left_empty():
    """Комментарии пишут руками. Выгрузка их не придумывает."""
    rows = [("TULANE", "Ivan Ivanov", date(2026, 9, 14), 7)]
    table = pivot(rows, weeks_in(date(2026, 9, 14), date(2026, 9, 20)))["TULANE"]
    assert table[1][-1] == ""


def test_components_shown_next_to_the_credited_days():
    """Случай Viacheslav Ochirov: засчитали 7, возил 2.

    Число засчитанных дней — пересечение двух условий, и в одной колонке
    ошибку увидеть нельзя. Слагаемые показываются рядом именно поэтому.
    """
    rows = [("COLUMBUS", "Viacheslav Ochirov", date(2026, 9, 28), 2)]
    totals = {"Viacheslav Ochirov": (2, 7, 77)}
    table = pivot(rows, weeks_in(date(2026, 9, 28), date(2026, 10, 4)), totals)["COLUMBUS"]

    assert table[0][-4:] == ["Возил дней", "В табеле", "Часов", "Комментарий"]
    assert table[1] == ["Viacheslav Ochirov", "2", "2", "7", "77", ""]


def test_driver_without_totals_still_renders():
    """Отсутствие слагаемых не должно ломать строку целиком."""
    rows = [("TULANE", "Ivan Ivanov", date(2026, 9, 14), 3)]
    table = pivot(rows, weeks_in(date(2026, 9, 14), date(2026, 9, 20)), {})["TULANE"]
    assert table[1] == ["Ivan Ivanov", "3", "", "", "", ""]


def test_hours_rendered_without_trailing_zeros():
    """77, а не 77.00 — в листе часы пишут целыми."""
    from svodka import _hours
    assert _hours(77) == "77"
    assert _hours(77.0) == "77"
    assert _hours(76.5) == "76.5"
    assert _hours(None) == ""
    assert _hours("") == ""


# ──────────────── выбор правила ────────────────

def test_three_modes_select_different_queries():
    """Правило — параметр, а не решение кода: цена разная.

    За 28.09–04.10 три правила дают 1089, 909 и 1260 дней. Подменить
    одно другим молча значит изменить выплаты, поэтому выбор явный.
    """
    import svodka

    assert "pool" in svodka.FAIR_SQL, "справедливое смотрит на пассажиров"
    assert "HAVING count(*) >= 2" in svodka.ROWS_SQL
    assert "drove" in svodka.BY_TIMESHEET_SQL or "rides" in svodka.BY_TIMESHEET_SQL


def test_fair_rule_requires_both_conditions():
    """Спорный день засчитывается только если ОБА условия выполнены."""
    import svodka

    sql = svodka.FAIR_SQL
    assert "c2.driver_id <> pr.person_id" in sql, "пассажир не ехал с другим"
    assert ">= 2" in sql, "минимум двое пассажиров на работе"
