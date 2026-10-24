"""Часовой журнал карточек и зачёт «стоял в карточке в любой час дня».

Решение пользователя 24.10: пассажир засчитывается за день, если он был
в карточке водителя хотя бы на одном часовом снимке этого дня, а не только
в 21:05. Журнал хранит лишь изменения, поэтому состояние на начало дня —
последняя запись до него.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.cardlog import changed_cards, merge_day_cards, suspicious_read  # noqa: E402

D0 = date(2026, 10, 23)
D1 = date(2026, 10, 24)
D2 = date(2026, 10, 25)
D3 = date(2026, 10, 26)

IVAN = 111


def cards(merged):
    """(дата, tg) → пассажиры — так проверять удобнее, чем список кортежей."""
    return {(d, tg): p for d, tg, _, p in merged}


# ───────────────────────── что пишется в журнал ─────────────────────────

def test_first_run_records_every_card():
    current = {1: ("Ivan Ivanov", ["Aa Aa"]), 2: ("Petr Petrov", [])}
    assert changed_cards({}, current) == [
        (1, "Ivan Ivanov", ["Aa Aa"]), (2, "Petr Petrov", [])]


def test_unchanged_card_is_not_written_again():
    state = {1: ("Ivan Ivanov", ["Aa Aa", "Bb Bb"])}
    assert changed_cards(state, dict(state)) == []


def test_reordered_passengers_are_not_a_change():
    """Порядок колонок Passenger1..4 не меняет, кто едет."""
    assert changed_cards({1: ("Ivan Ivanov", ["Aa Aa", "Bb Bb"])},
                         {1: ("Ivan Ivanov", ["Bb Bb", "Aa Aa"])}) == []


def test_reversed_name_is_not_a_change():
    assert changed_cards({1: ("Ivan Ivanov", ["Akmal Shah"])},
                         {1: ("Ivan Ivanov", ["Shah Akmal"])}) == []


def test_removed_passenger_is_a_change():
    assert changed_cards({1: ("Ivan Ivanov", ["Aa Aa", "Bb Bb"])},
                         {1: ("Ivan Ivanov", ["Aa Aa"])}) == [(1, "Ivan Ivanov", ["Aa Aa"])]


def test_driver_gone_from_sheet_is_logged_as_empty():
    """Иначе его последние пассажиры переносились бы на каждый следующий день."""
    assert changed_cards({1: ("Ivan Ivanov", ["Aa Aa"])}, {}) == [(1, "Ivan Ivanov", [])]


def test_gone_driver_with_empty_card_is_not_logged_again():
    assert changed_cards({1: ("Ivan Ivanov", [])}, {}) == []


# ───────────────────── защита от битого чтения листа ─────────────────────

def test_empty_read_is_suspicious():
    """Пустой ответ Google записал бы «все карточки опустели»."""
    assert suspicious_read(previous=300, current=0)


def test_half_the_cards_missing_is_suspicious():
    assert suspicious_read(previous=300, current=120)


def test_normal_read_is_fine():
    assert not suspicious_read(previous=300, current=297)
    assert not suspicious_read(previous=300, current=310)


def test_first_run_is_fine():
    assert not suspicious_read(previous=0, current=356)


# ─────────────────────── зачёт за любой час дня ───────────────────────

def test_without_log_snapshots_pass_through_unchanged():
    """До запуска журнала история не меняется ни на одну поездку."""
    snaps = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa", "Bb Bb"]),
             (D2, IVAN, "Ivan Ivanov", ["Aa Aa"])]
    assert merge_day_cards(snaps, [], end=D2) == snaps


def test_passenger_removed_before_the_evening_snapshot_still_counts():
    log = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa", "Bb Bb"]),
           (D2, IVAN, "Ivan Ivanov", ["Aa Aa"])]           # Bb убрали утром D2
    snaps = [(D2, IVAN, "Ivan Ivanov", ["Aa Aa"])]
    assert cards(merge_day_cards(snaps, log, end=D2))[(D2, IVAN)] == ["Aa Aa", "Bb Bb"]


def test_passenger_added_after_the_snapshot_counts_the_same_day():
    log = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa"]),
           (D1, IVAN, "Ivan Ivanov", ["Aa Aa", "Cc Cc"])]  # вписали в 22:00
    snaps = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa"])]
    assert cards(merge_day_cards(snaps, log, end=D1))[(D1, IVAN)] == ["Aa Aa", "Cc Cc"]


def test_snapshot_passengers_keep_their_seats_first():
    """Места 1..4 из снимка не сдвигаются: добавленные журналом идут следом."""
    log = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa", "Bb Bb"])]
    snaps = [(D1, IVAN, "Ivan Ivanov", ["Bb Bb"])]
    assert cards(merge_day_cards(snaps, log, end=D1))[(D1, IVAN)] == ["Bb Bb", "Aa Aa"]


def test_same_person_spelled_reversed_is_not_doubled():
    log = [(D1, IVAN, "Ivan Ivanov", ["Shah Akmal"])]
    snaps = [(D1, IVAN, "Ivan Ivanov", ["Akmal Shah"])]
    assert cards(merge_day_cards(snaps, log, end=D1))[(D1, IVAN)] == ["Akmal Shah"]


def test_unchanged_card_carries_over_to_days_without_entries():
    """Журнал пишет только изменения; карточка без правок действует и дальше.

    Заодно это запасной снимок: если вечерний 21:05 не записался (D2),
    день всё равно собирается из журнала.
    """
    log = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa", "Bb Bb"])]
    snaps = [(D3, IVAN, "Ivan Ivanov", ["Aa Aa", "Bb Bb"])]
    got = cards(merge_day_cards(snaps, log, end=D3))
    assert got[(D2, IVAN)] == ["Aa Aa", "Bb Bb"]
    assert got[(D3, IVAN)] == ["Aa Aa", "Bb Bb"]


def test_driver_removed_from_sheet_stops_carrying():
    log = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa"]),
           (D2, IVAN, "Ivan Ivanov", [])]
    got = cards(merge_day_cards([], log, end=D3))
    assert got[(D2, IVAN)] == ["Aa Aa"], "до удаления в D2 он ещё стоял"
    assert (D3, IVAN) not in got


def test_days_before_the_log_started_are_untouched():
    snaps = [(D0, IVAN, "Ivan Ivanov", ["Aa Aa"])]
    log = [(D2, IVAN, "Ivan Ivanov", ["Bb Bb"])]
    got = cards(merge_day_cards(snaps, log, end=D2))
    assert got[(D0, IVAN)] == ["Aa Aa"]
    assert (D1, IVAN) not in got


def test_nothing_is_carried_past_end():
    log = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa"])]
    assert set(cards(merge_day_cards([], log, end=D2))) == {(D1, IVAN), (D2, IVAN)}


def test_empty_card_creates_no_day():
    assert merge_day_cards([], [(D1, IVAN, "Ivan Ivanov", [])], end=D2) == []


def test_driver_name_taken_from_the_snapshot():
    log = [(D1, IVAN, "Ivan Ivanoff", ["Aa Aa"])]
    snaps = [(D1, IVAN, "Ivan Ivanov", ["Aa Aa"])]
    assert merge_day_cards(snaps, log, end=D1) == [(D1, IVAN, "Ivan Ivanov", ["Aa Aa"])]


# ─────────────────────── пропущенный вечерний снимок ───────────────────────

def test_restart_after_the_evening_time_without_a_snapshot_catches_up():
    from db.store import needs_catchup
    assert needs_catchup(datetime(2026, 10, 24, 21, 30), captured=False)


def test_no_catchup_when_the_snapshot_is_already_there():
    from db.store import needs_catchup
    assert not needs_catchup(datetime(2026, 10, 24, 21, 30), captured=True)


def test_no_catchup_before_the_evening_time():
    """Утренний снимок записал бы утреннее состояние за весь день."""
    from db.store import needs_catchup
    assert not needs_catchup(datetime(2026, 10, 24, 20, 59), captured=False)


def test_catchup_time_matches_the_schedule():
    from db.store import CAPTURE_AT
    assert CAPTURE_AT == time(21, 5)
