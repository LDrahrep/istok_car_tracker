"""Тесты разбора списка пассажиров из сообщения водителя.

Раньше бот делил текст только по строкам. Водитель Mingian Mukranov
написал «Mariia Tcyrenzhapova, Bator↵Tcyrenzhapov» — через запятую и с
переносом внутри второго имени — и получил «Никого не удалось добавить»:
бот искал сотрудников «Mariia Tcyrenzhapova, Bator» и «Tcyrenzhapov».
"""
from __future__ import annotations

from models import split_passenger_names as split


def test_one_name_per_line_still_works():
    assert split("Ivan Ivanov\nMaria Ivanova") == ["Ivan Ivanov", "Maria Ivanova"]


def test_comma_separated_on_one_line():
    assert split("Ivan Ivanov, Maria Ivanova") == ["Ivan Ivanov", "Maria Ivanova"]


def test_line_break_inside_name_after_comma():
    # Ровно то сообщение, что пришло от водителя 11.10.
    text = "Mariia Tcyrenzhapova, Bator\nTcyrenzhapov"
    assert split(text) == ["Mariia Tcyrenzhapova", "Bator Tcyrenzhapov"]


def test_comma_at_line_end():
    assert split("Ivan Ivanov,\nMaria Ivanova") == ["Ivan Ivanov", "Maria Ivanova"]


def test_semicolon_separates_like_comma():
    assert split("Ivan Ivanov; Maria Ivanova") == ["Ivan Ivanov", "Maria Ivanova"]


def test_blank_parts_and_extra_spaces_are_dropped():
    assert split("  Ivan   Ivanov ,\n\n Maria Ivanova , ") == ["Ivan Ivanov", "Maria Ivanova"]
    assert split("\n Ivan Ivanov \n\n  Maria Ivanova\n") == ["Ivan Ivanov", "Maria Ivanova"]


def test_empty_message_gives_no_names():
    assert split("") == []
    assert split(" ,\n; ") == []
