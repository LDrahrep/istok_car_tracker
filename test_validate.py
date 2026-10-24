"""Тесты проверки пассажиров.

Раньше эта функция была непокрыта: она возвращала готовые русские строки,
и проверять было нечего, кроме текста. С кодами причин у неё появился
машинный ответ — и вместе с ним смысл в тестах.

Зависимости от Google подменяются: берётся несозданный экземпляр
SheetManager, методам доступа к таблицам подставляются заглушки. Проверяется
именно логика отбора, а не работа с сетью.
"""
from __future__ import annotations

from models import Driver, Employee, Reason, ShiftType
from sheets import SheetManager

DRIVER = Driver(name="Aldar Rakshaev", tg_id=111, shift="Day")


def mgr(driver=DRIVER, employees=(), *, drivers_by_name=None,
        own_counts=None, carpool=None, sheet_employees=None):
    m = SheetManager.__new__(SheetManager)
    employees = list(employees)
    m.get_driver = lambda tg: driver
    m.get_all_employees = lambda: employees
    # Лист сотрудников: по умолчанию тот же справочник, что и база.
    m.employees_from_sheet = lambda: list(
        employees if sheet_employees is None else sheet_employees)
    m.get_employee_by_name = lambda n: next(
        (e for e in employees if e.name.casefold() == (n or "").casefold()), None)
    m.get_driver_by_name = lambda n: (drivers_by_name or {}).get(n)
    m.driver_own_passenger_count = lambda n: (own_counts or {}).get(n, 0)
    m.find_driver_for_passenger = lambda n: (carpool or {}).get(n)
    return m


def codes(reasons):
    return [r.code for r in reasons]


# ─────────────────────── главная регрессия ───────────────────────

def test_nothing_returns_a_bare_string():
    """Готовая строка из слоя данных = смешанный язык в ответе.

    Англоязычный водитель получал часть ответа по-английски из локали,
    часть по-русски прямо из sheets.py. Тест держит границу: отсюда
    выходят только коды.
    """
    _, errors, warnings = mgr(employees=[]).validate_passengers(111, ["Кто-то"])
    for r in errors + warnings:
        assert isinstance(r, Reason), f"строка вместо кода причины: {r!r}"


# ─────────────────────── блокирующие отказы ───────────────────────

def test_not_registered_as_driver():
    _, errors, _ = mgr(driver=None).validate_passengers(999, ["Ivan Ivanov"])
    assert codes(errors) == ["validate.not_a_driver"]


def test_driver_without_shift():
    """Смены нет ни в drivers, ни в employees — добавлять некого и некуда."""
    d = Driver(name="Aldar Rakshaev", tg_id=111, shift="")
    _, errors, _ = mgr(driver=d).validate_passengers(111, ["Ivan Ivanov"])
    assert codes(errors) == ["driver.shift_unknown"]


# ─────────────────────── отказы по конкретным людям ───────────────────────

def test_unknown_name_reports_name_back():
    _, _, warnings = mgr(employees=[]).validate_passengers(111, ["Нет Такого"])
    assert codes(warnings) == ["passenger_warning.not_found"]
    assert warnings[0].params["name"] == "Нет Такого"


def test_close_name_gets_suggestions():
    """Опечатка в имени — частый случай, подсказка экономит переписку."""
    emps = [Employee(name="Badma Matsakov", shift="Day")]
    _, _, warnings = mgr(employees=emps).validate_passengers(111, ["Badma Matsakoff"])
    assert codes(warnings) == ["passenger_warning.not_found_suggest"]
    assert "Badma Matsakov" in warnings[0].params["suggestions"]
    assert warnings[0].params["name"] == "Badma Matsakoff", "в тексте — то, что ввёл водитель"


def test_other_shift_is_rejected():
    emps = [Employee(name="Night Guy", shift="Night")]
    _, _, warnings = mgr(employees=emps).validate_passengers(111, ["Night Guy"])
    assert codes(warnings) == ["passenger_warning.wrong_shift"]


def test_driver_cannot_add_himself():
    emps = [Employee(name="Aldar Rakshaev", shift="Day")]
    _, _, warnings = mgr(employees=emps).validate_passengers(111, ["Aldar Rakshaev"])
    assert codes(warnings) == ["passenger_warning.self"]


def test_taken_by_another_driver_names_him():
    """Имя чужого водителя нужно в тексте — иначе непонятно, к кому идти."""
    emps = [Employee(name="Badma Matsakov", shift="Day", rides_with="Petr Petrov")]
    _, _, warnings = mgr(employees=emps, carpool={"Badma Matsakov": (222, "Petr Petrov")}
                         ).validate_passengers(111, ["Badma Matsakov"])
    assert codes(warnings) == ["passenger_warning.already_with_driver"]
    assert warnings[0].params["driver"] == "Petr Petrov"


def test_active_driver_cannot_be_a_passenger():
    """У него есть свои пассажиры — он за рулём, а не едет."""
    other = Driver(name="Badma Matsakov", tg_id=222, shift="Day")
    emps = [Employee(name="Badma Matsakov", shift="Day")]
    _, _, warnings = mgr(
        employees=emps,
        drivers_by_name={"Badma Matsakov": other},
        own_counts={"Badma Matsakov": 2},
    ).validate_passengers(111, ["Badma Matsakov"])
    assert codes(warnings) == ["passenger_warning.is_active_driver"]


# ─────────────────────── успешный путь ───────────────────────

def test_free_employee_is_accepted():
    emps = [Employee(name="Badma Matsakov", shift="Day")]
    valid, errors, warnings = mgr(employees=emps).validate_passengers(
        111, ["Badma Matsakov"])
    assert [e.name for e in valid] == ["Badma Matsakov"]
    assert not errors and not warnings


def test_reversed_name_order_still_matches():
    """В табелях имя и фамилию регулярно меняют местами."""
    emps = [Employee(name="Matsakov Badma", shift="Day")]
    valid, _, _ = mgr(employees=emps).validate_passengers(111, ["Badma Matsakov"])
    assert [e.name for e in valid] == ["Matsakov Badma"]


def test_more_than_four_are_cut():
    emps = [Employee(name=f"Pass {i}", shift="Day") for i in range(6)]
    valid, _, warnings = mgr(employees=emps).validate_passengers(
        111, [f"Pass {i}" for i in range(6)])
    assert len(valid) == 4
    assert "passenger_warning.too_many" in codes(warnings)


def test_already_assigned_to_me():
    emps = [Employee(name="Badma Matsakov", shift="Day", tg_id=111)]
    valid, errors, _ = mgr(employees=emps, carpool={"Badma Matsakov": (111, "Aldar Rakshaev")}
                           ).validate_passengers(111, ["Badma Matsakov"])
    assert not valid
    assert codes(errors) == ["validate.all_already_yours"]
    assert "Badma Matsakov" in errors[0].params["names"]


def test_nobody_added_when_all_rejected():
    emps = [Employee(name="Night Guy", shift="Night")]
    valid, errors, warnings = mgr(employees=emps).validate_passengers(111, ["Night Guy"])
    assert not valid
    assert codes(errors) == ["validate.nobody_added"]
    assert codes(warnings) == ["passenger_warning.wrong_shift"]


def test_wrong_shift_reason_carries_both_shifts():
    """«В другой смене» без указания смен не объясняет отказ.

    Админ не видит, чья сторона устарела. Живой случай: Артур Альтерман
    перешёл на дневную, бот двое суток отказывал «в другой смене», и
    понять причину по сообщению было невозможно.
    """
    emps = [Employee(name="Night Guy", shift="Night")]
    _, _, warnings = mgr(employees=emps).validate_passengers(111, ["Night Guy"])

    (причина,) = warnings
    assert причина.code == "passenger_warning.wrong_shift"
    assert причина.params["driver_shift"] == "day"
    assert причина.params["passenger_shift"] == "night"


def test_wrong_shift_message_renders_without_leftover_braces():
    """Нехватка подстановки заставляет t() вернуть шаблон как есть —
    и в чат уезжает «{driver_shift}». Проверяем, что все заполнены."""
    from locales import ru

    emps = [Employee(name="Night Guy", shift="Night")]
    _, _, warnings = mgr(employees=emps).validate_passengers(111, ["Night Guy"])
    текст = ru.STRINGS[warnings[0].code].format(**warnings[0].params)
    assert "{" not in текст


# ─────────────────── смена водителя по telegramID ───────────────────
# 11.10 рассылка проверки Day ушла 0 водителям. Справочник из базы берёт
# DriverTGID только из сегодняшних поездок, а утром их ещё нет; самому
# водителю такой записи в базе нет никогда.

def test_driver_shift_found_without_todays_rides():
    m = mgr(employees=[Employee(name="Aldar Rakshaev", shift="Day")])
    assert m.get_shift_for_tgid(111) == ShiftType.DAY


def test_driver_shift_falls_back_to_employees_when_drivers_blank():
    d = Driver(name="Aldar Rakshaev", tg_id=111, shift="")
    emps = [Employee(name="Passenger One", shift="Night", tg_id=111)]
    assert mgr(driver=d, employees=emps).get_shift_for_tgid(111) == ShiftType.NIGHT


def test_unknown_driver_has_unknown_shift():
    assert mgr(driver=None, employees=[]).get_shift_for_tgid(999) == ShiftType.UNKNOWN


# ─────────── чужая карточка, имя с другим порядком слов ───────────
# 28.09–03.10 Akmal Shah неделю стоял в двух карточках: у Kurchabekov Alibek
# как «Akmal Shah», у Amonullo Jomikov как «Shah Akmal». Бот сравнивал имена
# буквально и дубль пропустил, а база отдала пассажира одному из них.

def test_reversed_name_found_in_another_card():
    from types import SimpleNamespace

    m = SheetManager.__new__(SheetManager)
    m.config = SimpleNamespace(DRIVERS_PASSENGERS_SHEET="drivers_passengers")
    m._values = lambda name: [
        ["Name", "telegramID", "Phone Number", "Shift",
         "Passenger1", "Passenger2", "Passenger3", "Passenger4"],
        ["Kurchabekov Alibek", "555", "", "Day",
         "Akmal Shah", "Sarboz Gulomaliev", "", ""],
    ]
    assert m.find_driver_for_passenger("Shah Akmal") == (555, "Kurchabekov Alibek")


# ─────────── «едет с» в справочнике устарело, карточка — правда ───────────
# Справочник при USE_DB_READS берёт «едет с» из сегодняшних поездок в базе,
# а они отстают от карточек: ночная проверка чистит список уже после снимка
# в 21:05, ручная правка листа не снимает дневные записи бота. 19.10 водители
# видели пустую «Мою запись» и при добавлении слышали «уже приписаны к тебе»,
# а Maksim Telkov числился «у Magomed», хотя в его карточке давно не стоял.

def test_stale_link_to_me_does_not_block_readding():
    emps = [Employee(name="Farhad Yskakov", shift="Day",
                     rides_with="Aldar Rakshaev", tg_id=111)]
    valid, errors, warnings = mgr(employees=emps, carpool={}).validate_passengers(
        111, ["Farhad Yskakov"])
    assert [e.name for e in valid] == ["Farhad Yskakov"]
    assert not errors and not warnings


def test_stale_link_to_other_driver_does_not_block():
    emps = [Employee(name="Maksim Telkov", shift="Day",
                     rides_with="Magomed Tovsultanov", tg_id=222)]
    valid, _, warnings = mgr(employees=emps, carpool={}).validate_passengers(
        111, ["Maksim Telkov"])
    assert [e.name for e in valid] == ["Maksim Telkov"]
    assert not warnings


def test_card_wins_when_directory_names_another_driver():
    """Справочник отстал в другую сторону: в карточке уже другой водитель."""
    emps = [Employee(name="Badma Matsakov", shift="Day",
                     rides_with="Petr Petrov", tg_id=222)]
    _, _, warnings = mgr(employees=emps, carpool={"Badma Matsakov": (333, "Ivan Sidorov")}
                         ).validate_passengers(111, ["Badma Matsakov"])
    assert codes(warnings) == ["passenger_warning.already_with_driver"]
    assert warnings[0].params["driver"] == "Ivan Sidorov"


# ─────────────── случаи из поддержки 20–24.10 ───────────────

def _emp(name, shift="Day"):
    return Employee(name=name, phone="", shift=shift)


def test_driver_typing_himself_in_reverse_order_is_self():
    """«Rakshaev Aldar» у водителя «Aldar Rakshaev» — это он сам, а не пассажир.
    Раньше проверка сравнивала сырой ввод, и водитель записывался сам себе."""
    valid, _, warnings = mgr(employees=[_emp("Aldar Rakshaev")]).validate_passengers(
        111, ["Rakshaev Aldar"])
    assert valid == []
    assert codes(warnings) == ["passenger_warning.self"]


def test_accent_in_input_still_matches():
    """Водитель набирает «Jesús», в ростере «Jesus»."""
    valid, _, _ = mgr(employees=[_emp("Jesus Perez")]).validate_passengers(
        111, ["Jesús Perez"])
    assert [e.name for e in valid] == ["Jesus Perez"]


def test_accent_in_roster_matches_plain_input():
    """Наоборот: в ростере «Jesús García», а «ú» на телефоне не набрать."""
    valid, _, _ = mgr(employees=[_emp("Jesús García")]).validate_passengers(
        111, ["Jesus Garcia"])
    assert [e.name for e in valid] == ["Jesús García"]


def test_suggestion_survives_reversed_order_and_typo():
    """«Abdurahimov Dzhamshed» при «Dzhamshed Abdurakhimov»: раньше подсказка
    сравнивала сырые строки (0.42) и предлагала чужих людей."""
    _, _, warnings = mgr(employees=[_emp("Dzhamshed Abdurakhimov"),
                                    _emp("Dzhamshed Mamadatoev")]).validate_passengers(
        111, ["Abdurahimov Dzhamshed"])
    assert codes(warnings) == ["passenger_warning.not_found_suggest"]
    assert warnings[0].params["options"][0] == "Dzhamshed Abdurakhimov"


def test_suggestion_by_surname_when_first_name_differs():
    """«Enrique Cisneros» — второе имя человека, который записан «Asdrubal Cisneros»."""
    _, _, warnings = mgr(employees=[_emp("Asdrubal Cisneros")]).validate_passengers(
        111, ["Enrique Cisneros"])
    assert codes(warnings) == ["passenger_warning.not_found_suggest"]
    assert warnings[0].params["options"] == ["Asdrubal Cisneros"]


def test_suggestion_from_other_shift_is_named_but_not_offered_as_button():
    """Человек из другой смены: имя подсказываем, но кнопкой не предлагаем —
    добавить его всё равно нельзя."""
    _, _, warnings = mgr(employees=[_emp("Ivan Petrov", "Night")]).validate_passengers(
        111, ["Ivan Petrof"])
    assert codes(warnings) == ["passenger_warning.not_found_suggest"]
    assert "Ivan Petrov" in warnings[0].params["suggestions"]
    assert warnings[0].params["options"] == []


def test_new_hire_missing_in_db_is_found_in_the_sheet():
    """Человека внесли в Car Bot, а ежечасная синхронизация ещё не прошла:
    в базе его нет, в листе сотрудников — есть."""
    m = mgr(employees=[_emp("Old Timer")],
            sheet_employees=[_emp("Old Timer"), _emp("New Hire")])
    valid, _, _ = m.validate_passengers(111, ["New Hire"])
    assert [e.name for e in valid] == ["New Hire"]


def test_same_person_twice_in_one_message_counts_once():
    """«Badma Matsakov, Matsakov Badma» — один человек, одно место."""
    valid, _, _ = mgr(employees=[_emp("Badma Matsakov")]).validate_passengers(
        111, ["Badma Matsakov", "Matsakov Badma"])
    assert [e.name for e in valid] == ["Badma Matsakov"]
