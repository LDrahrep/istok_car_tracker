"""Тесты проверки пассажиров.

Раньше эта функция была непокрыта: она возвращала готовые русские строки,
и проверять было нечего, кроме текста. С кодами причин у неё появился
машинный ответ — и вместе с ним смысл в тестах.

Зависимости от Google подменяются: берётся несозданный экземпляр
SheetManager, методам доступа к таблицам подставляются заглушки. Проверяется
именно логика отбора, а не работа с сетью.
"""
from __future__ import annotations

from models import Driver, Employee, Reason
from sheets import SheetManager

DRIVER = Driver(name="Aldar Rakshaev", tg_id=111, shift="Day")


def mgr(driver=DRIVER, employees=(), *, drivers_by_name=None,
        own_counts=None, carpool=None):
    m = SheetManager.__new__(SheetManager)
    employees = list(employees)
    m.get_driver = lambda tg: driver
    m.get_all_employees = lambda: employees
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
    _, _, warnings = mgr(employees=emps).validate_passengers(111, ["Badma Matsakov"])
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
    valid, errors, _ = mgr(employees=emps).validate_passengers(111, ["Badma Matsakov"])
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
