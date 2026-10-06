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


# ──────────────────────── сборка ростера ────────────────────────

from importer import (  # noqa: E402
    PersonRow,
    build_people,
    is_timesheet,
    resolve_telegram_conflicts,
    sites_referenced,
)


def _by_key(people):
    return {p.name_key: p for p in people}


def test_own_telegram_id_comes_from_drivers_not_employees():
    """Регрессия на главную ловушку этих данных.

    В листе employees колонка DriverTGID — это ID ВОДИТЕЛЯ, с которым едет
    сотрудник (models.Employee.from_row). Если импорт возьмёт её за личный
    telegram_id, каждый пассажир получит ID своего водителя, а person.telegram_id
    объявлен UNIQUE — вставка упадёт, причём не на первом пассажире.
    Личный ID есть только в листе drivers.
    """
    employees = [("Aldar Rakshaev", "Day"), ("Ismail Gasanov", "Day")]
    drivers = {name_key("Aldar Rakshaev"): 111}
    people = _by_key(build_people(employees, drivers, []))

    assert people[name_key("Aldar Rakshaev")].telegram_id == 111
    assert people[name_key("Ismail Gasanov")].telegram_id is None


def test_employees_spelling_wins_over_timesheet():
    """В табелях имена пишут капсом и вперемешку, в employees — аккуратно."""
    employees = [("Bobo Rahmonzoda", "Night")]
    presence = [PresenceRow("BOBO RAHMONZODA", date(2026, 10, 1), "AMAZON")]
    people = _by_key(build_people(employees, {}, presence))

    assert people[name_key("Bobo Rahmonzoda")].full_name == "Bobo Rahmonzoda"


def test_timesheet_only_person_is_kept():
    """Человек есть в табеле и отсутствует в employees — это рассинхрон.

    Терять его на импорте нельзя: именно такие строки администратор и правит
    руками, а чтобы править, их надо сначала увидеть.
    """
    presence = [PresenceRow("Novyi Chelovek", date(2026, 10, 1), "BUFFALO")]
    people = _by_key(build_people([], {}, presence))

    row = people[name_key("Novyi Chelovek")]
    assert row.shift == "unknown"
    assert row.current_site_id == "BUFFALO"


def test_shift_from_employees_site_from_presence():
    """«Meltech Day» даёт смену, но объект достовернее из последней отметки."""
    employees = [("Ivan Ivanov", "Meltech Day")]
    presence = [PresenceRow("Ivan Ivanov", date(2026, 9, 1), "MELTECH"),
                PresenceRow("Ivan Ivanov", date(2026, 10, 1), "BUFFALO")]
    row = _by_key(build_people(employees, {}, presence))[name_key("Ivan Ivanov")]

    assert row.shift == "day"
    assert row.current_site_id == "BUFFALO"


def test_site_falls_back_to_shift_string_without_presence():
    employees = [("Petr Petrov", "Meltech Night")]
    row = _by_key(build_people(employees, {}, []))[name_key("Petr Petrov")]

    assert row.shift == "night"
    assert row.current_site_id == "MELTECH"


def test_unknown_shift_does_not_erase_known_one():
    """Табель знает человека, но не знает его смену — не повод её терять."""
    employees = [("Ivan Ivanov", "Night")]
    presence = [PresenceRow("Ivan Ivanov", date(2026, 10, 1), "AMAZON")]
    row = _by_key(build_people(employees, {}, presence))[name_key("Ivan Ivanov")]

    assert row.shift == "night"


# ──────────────── конфликт telegram_id ────────────────

def test_duplicate_telegram_id_is_resolved_not_fatal():
    """Переименовали водителя — старая строка в drivers осталась.

    Один ID на два name_key нарушает UNIQUE и роняет всю пачку. Оставляем
    первому, у второго снимаем, и обязательно сообщаем: иначе администратор
    не узнает, что в источнике две строки на одного человека.
    """
    people = [
        PersonRow("Ivan Ivanov", name_key("Ivan Ivanov"), "day", 500, None),
        PersonRow("Ivan Ivanoff", name_key("Ivan Ivanoff"), "day", 500, None),
    ]
    people, conflicts = resolve_telegram_conflicts(people)

    assert people[0].telegram_id == 500
    assert people[1].telegram_id is None
    assert conflicts == [(name_key("Ivan Ivanov"), name_key("Ivan Ivanoff"), 500)]


def test_distinct_telegram_ids_untouched():
    people = [
        PersonRow("A A", name_key("A A"), "day", 1, None),
        PersonRow("B B", name_key("B B"), "day", 2, None),
        PersonRow("C C", name_key("C C"), "day", None, None),
    ]
    people, conflicts = resolve_telegram_conflicts(people)

    assert not conflicts
    assert [p.telegram_id for p in people] == [1, 2, None]


# ──────────────── справочник объектов ────────────────

def test_sites_collected_from_both_sources():
    """site_id в person и presence — внешние ключи, справочник нужен раньше них."""
    people = [PersonRow("A A", "a a", "day", None, "MELTECH")]
    presence = [PresenceRow("B B", date(2026, 10, 1), "BUFFALO")]

    assert sites_referenced(people, presence) == ["BUFFALO", "MELTECH"]


def test_sites_skip_missing_values():
    people = [PersonRow("A A", "a a", "unknown", None, None)]
    assert sites_referenced(people, []) == []


# ──────────────── отбор листов ────────────────

def test_only_dated_site_sheets_are_imported():
    assert is_timesheet(BUFFALO_SHEET)
    assert is_timesheet("8242026-8302026 AMAZON")


def test_sheets_without_dates_are_skipped():
    """«PHASE 5 AMAZON»: объект есть, а к какой неделе колонки — неизвестно."""
    assert not is_timesheet("PHASE 5 AMAZON")
    assert not is_timesheet("Svodka Columbus")
    assert not is_timesheet("employees")


# ──────────── разбор диапазона дат: неоднозначный формат ────────────

def test_unpadded_month_is_parsed():
    """Регрессия: «8242026» читался как месяц 82.

    Жадный `\\d{1,2}` в прежнем регекспе отдавал разбор 82|4|2026, date()
    бросал ValueError, функция возвращала None — и parse_timesheet уходил на
    даты из шапки, которые копируют с чужой недели. Присутствие датировалось
    неверной неделей молча.
    """
    dates = week_dates_from_name("8242026-8302026 AMAZON")
    assert dates is not None, "месяц без ведущего нуля должен разбираться"
    assert dates[0] == date(2026, 8, 24)
    assert dates[-1] == date(2026, 8, 30)


def test_week_dates_cross_year():
    """Конец декабря: проверка «конец = начало + 6» должна выдержать смену года."""
    dates = week_dates_from_name("12312025-01062026 AMAZON")
    assert dates[0] == date(2025, 12, 31)
    assert dates[-1] == date(2026, 1, 6)


def test_week_dates_unequal_halves():
    """Один конец диапазона с ведущим нулём, другой без."""
    assert week_dates_from_name("8242026-09202026 AMAZON")[0] == date(2026, 8, 24)


def test_week_dates_always_seven_days():
    for name in ("09142026-09202026 BUFFALO", "8242026-8302026 AMAZON",
                 "8/17/2026-8/23/2026 AMAZON", "12312025-01062026 AMAZON"):
        dates = week_dates_from_name(name)
        assert len(dates) == 7, name
        assert (dates[-1] - dates[0]).days == 6, name


# ──────────────── связь водитель↔пассажир ────────────────

from importer import CarpoolLink, build_carpool  # noqa: E402

D1, D2, P1, P2 = 10, 20, 31, 32
KEYS = {name_key("Driver One"): D1, name_key("Driver Two"): D2,
        name_key("Pass One"): P1, name_key("Pass Two"): P2}
TGIDS = {111: D1, 222: D2}
DAY = date(2026, 10, 1)


def test_passengers_resolve_to_person_ids():
    """Главный смысл таблицы: имя превращается в ссылку один раз."""
    rows, bad = build_carpool(
        [(DAY, 111, "Driver One", ["Pass One", "Pass Two"])], KEYS, TGIDS)

    assert rows == [CarpoolLink(DAY, D1, P1, 1), CarpoolLink(DAY, D1, P2, 2)]
    assert not any(bad.values())


def test_driver_resolved_by_telegram_id_first():
    """Имя водителя в снапшоте может быть написано иначе, чем в ростере.

    telegram_id — точный ключ, имя — нет, поэтому сначала пробуем ID.
    """
    rows, bad = build_carpool(
        [(DAY, 111, "ДРУГОЕ НАПИСАНИЕ", ["Pass One"])], KEYS, TGIDS)

    assert rows[0].driver_id == D1
    assert not bad["driver"]


def test_seats_are_numbered_from_one_without_gaps():
    """Номера мест держат вместимость: UNIQUE(дата, водитель, место)."""
    rows, _ = build_carpool(
        [(DAY, 111, "Driver One", ["Нет Такого", "Pass One", "Pass Two"])],
        KEYS, TGIDS)

    assert [r.seat for r in rows] == [1, 2], "пропущенный пассажир не должен съедать место"


def test_unresolved_passenger_is_reported_not_dropped():
    """Молча потерянная строка — ровно та беда, из-за которой всё затевалось."""
    rows, bad = build_carpool(
        [(DAY, 111, "Driver One", ["Ivann Ivanovv"])], KEYS, TGIDS)

    assert rows == []
    assert bad["passenger"] == [(DAY, "Ivann Ivanovv")]


def test_passenger_with_two_drivers_same_day_is_cut():
    """Первичный ключ (дата, пассажир) запрещает двух водителей.

    Если не отсечь здесь, вставка упадёт целиком — вместе со всеми
    остальными связями этого прогона.
    """
    rows, bad = build_carpool([
        (DAY, 111, "Driver One", ["Pass One"]),
        (DAY, 222, "Driver Two", ["Pass One"]),
    ], KEYS, TGIDS)

    assert len(rows) == 1 and rows[0].driver_id == D1
    assert len(bad["taken"]) == 1


def test_same_passenger_on_different_days_is_fine():
    """Ограничение — на день, а не навсегда: завтра можно ехать с другим."""
    rows, bad = build_carpool([
        (DAY, 111, "Driver One", ["Pass One"]),
        (date(2026, 10, 2), 222, "Driver Two", ["Pass One"]),
    ], KEYS, TGIDS)

    assert len(rows) == 2
    assert not bad["taken"]


def test_driver_listing_himself_is_cut():
    """CHECK (driver_id <> passenger_id) — иначе вставка упала бы."""
    rows, bad = build_carpool(
        [(DAY, 111, "Driver One", ["Driver One"])], KEYS, TGIDS)

    assert rows == []
    assert bad["self"] == [(DAY, "Driver One")]


def test_unknown_driver_skips_whole_row():
    rows, bad = build_carpool(
        [(DAY, 999, "Никому Неизвестный", ["Pass One"])], KEYS, TGIDS)

    assert rows == []
    assert bad["driver"] == [(DAY, "Никому Неизвестный")]
    assert not bad["passenger"], "пассажиров без водителя разбирать незачем"


def test_empty_passenger_list_is_not_an_error():
    rows, bad = build_carpool([(DAY, 111, "Driver One", [])], KEYS, TGIDS)
    assert rows == [] and not any(bad.values())


# ──────────────── диагностика пропущенных листов ────────────────

from importer import suspicious_sheets  # noqa: E402


def test_real_timesheets_are_not_reported_as_skipped():
    assert suspicious_sheets([BUFFALO_SHEET, "8/17/2026-8/23/2026 AMAZON"]) == []


def test_plain_sheets_are_not_reported():
    """employees и Svodka — заведомо не табели, шуметь про них незачем."""
    assert suspicious_sheets(["employees", "drivers", "Svodka Columbus", "week2"]) == []


def test_site_sheet_without_dates_is_flagged():
    """«PHASE 5 AMAZON»: объект есть, недели нет — присутствие потеряется молча."""
    (title, reason), = suspicious_sheets(["PHASE 5 AMAZON"])
    assert title == "PHASE 5 AMAZON"
    assert "дат" in reason


def test_dated_sheet_with_unknown_site_is_flagged():
    """Новый объект, которого нет в SITE_ALIASES, — самая дорогая потеря."""
    (title, reason), = suspicious_sheets(["09212026-09272026 NEWSITE"])
    assert "объект" in reason
