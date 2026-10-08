"""Тесты сверки таблицы и базы."""
from __future__ import annotations

import os
import sys

# Импорт пакетом, а не плоско: mirror.py пользуется относительными
# импортами, и как модуль верхнего уровня он не соберётся.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.mirror import diff_states  # noqa: E402


def test_identical_states_give_no_difference():
    state = {1: ["Ivan Ivanov", "Petr Petrov"]}
    assert diff_states(state, dict(state)) == {
        "only_sheet": [], "only_db": [], "differs": []}


def test_reversed_name_is_not_a_difference():
    """«Akmal Shah» и «Shah Akmal» — один человек, а не расхождение."""
    assert diff_states({1: ["Akmal Shah"]}, {1: ["Shah Akmal"]})["differs"] == []


def test_case_and_spacing_ignored():
    assert diff_states({1: ["BOBO  RAHMONZODA"]},
                       {1: ["Bobo Rahmonzoda"]})["differs"] == []


def test_missing_in_db_is_reported():
    d = diff_states({1: ["Ivan Ivanov"]}, {})
    assert d["only_sheet"] == [(1, ["Ivan Ivanov"])]


def test_extra_in_db_is_reported():
    """Лишнее в базе опаснее недостающего: значит зеркало что-то выдумало."""
    d = diff_states({}, {1: ["Ivan Ivanov"]})
    assert d["only_db"] == [(1, ["Ivan Ivanov"])]


def test_different_passengers_are_reported():
    d = diff_states({1: ["Ivan Ivanov"]}, {1: ["Petr Petrov"]})
    assert len(d["differs"]) == 1


def test_empty_list_both_sides_is_not_a_difference():
    assert diff_states({1: []}, {1: []})["differs"] == []


def test_empty_strings_do_not_count_as_passengers():
    assert diff_states({1: ["", "  "]}, {1: []})["differs"] == []
