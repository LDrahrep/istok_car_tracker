"""Тесты открепления пассажира от водителя.

08.11 Kamol Azizov нажал «Я больше не еду с водителем», получил «Готово»,
а в карточке водителя остался: там он был записан «Azizov Kamol», а в
справочнике — «Kamol Azizov». Водителя бот находил без учёта порядка слов,
а вычёркивал только точное написание — и молча ничего не менял.

Зависимости от Google подменяются, как в test_validate.py.
"""
from __future__ import annotations

from models import DriverPassengers
from sheets import SheetManager

OLD_DRIVER = 555


def mgr(card, *, driver_name="Avazbek Avzalkhonov"):
    m = SheetManager.__new__(SheetManager)
    m.saved, m.cleared = [], []
    m.find_driver_for_passenger = lambda n: (OLD_DRIVER, driver_name)
    m.get_driver_passengers = lambda tg: DriverPassengers(driver_name, OLD_DRIVER, list(card))
    m.upsert_driver_passengers = lambda dp: m.saved.append(list(dp.passengers))
    m.clear_rides_with = lambda *, names: m.cleared.append(set(names)) or 1
    return m


def test_unlink_ignores_word_order_in_the_card():
    m = mgr(["Azizov Kamol", "Osman Gadzhiev"])

    hit = m.unlink_passenger_everywhere("Kamol Azizov", except_tgid=0)

    assert hit == (OLD_DRIVER, "Avazbek Avzalkhonov")
    assert m.saved == [["Osman Gadzhiev"]]


def test_unlink_clears_rides_with_under_both_spellings():
    # В листе сотрудников человек может стоять под любым из двух написаний.
    m = mgr(["Azizov Kamol", "Osman Gadzhiev"])

    m.unlink_passenger_everywhere("Kamol Azizov", except_tgid=0)

    assert m.cleared == [{"Kamol Azizov", "Azizov Kamol"}]


def test_driver_in_his_own_card_under_reversed_name_is_left_alone():
    # Самоприписку («X едет с X») за карпул не считаем — и в обратном порядке тоже.
    m = mgr(["Kamol Azizov", "Osman Gadzhiev"], driver_name="Azizov Kamol")

    assert m.unlink_passenger_everywhere("Kamol Azizov", except_tgid=0) is None
    assert m.saved == []
