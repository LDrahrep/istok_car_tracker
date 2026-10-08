"""Тесты разбора телефона.

Случаи взяты из живых данных листа drivers, а не придуманы.
"""
from __future__ import annotations

import pytest

from phones import looks_like_phone, normalize


@pytest.mark.parametrize("raw,expected", [
    ("7187152475", "(718) 715-2475"),
    ("9295948802", "(929) 594-8802"),
    ("+1 408 555 0101", "(408) 555-0101"),
    ("1 (718) 715-2475", "(718) 715-2475"),
    ("718-715-2475", "(718) 715-2475"),
    ("718.715.2475", "(718) 715-2475"),
    ("  9174705981  ", "(917) 470-5981"),
])
def test_us_numbers_normalised(raw, expected):
    assert normalize(raw) == expected


def test_passenger_list_in_phone_column_is_rejected():
    """Реальный случай: в поле телефона оказался список пассажиров."""
    garbage = ("ПАССАЖИРЫ. SHARAFJON YULDOSHEV ISLAM TOSHTEMUROV "
               "ABDUAKHADKHON IBRAGIMOV  AKBAR PULATOV")
    assert normalize(garbage) is None
    assert not looks_like_phone(garbage)


@pytest.mark.parametrize("raw", ["", "   ", None, "нет", "—", "n/a"])
def test_empty_and_placeholders_are_rejected(raw):
    assert normalize(raw) is None


@pytest.mark.parametrize("raw", ["12345", "123456789012345678", "0"])
def test_implausible_digit_counts_are_rejected(raw):
    """Пять цифр — не номер, восемнадцать — тоже."""
    assert normalize(raw) is None


def test_international_kept_with_plus():
    assert normalize("+992 93 123 4567") == "+992931234567"
    assert normalize("+44 20 7946 0958") == "+442079460958"


def test_international_without_plus_is_not_guessed():
    """Без плюса одиннадцать цифр не из США — угадывать страну нельзя."""
    assert normalize("79991234567") is None


def test_name_with_digits_is_not_mistaken_for_phone():
    assert normalize("Ivan Ivanov") is None
    assert normalize("квартира 12") is None
