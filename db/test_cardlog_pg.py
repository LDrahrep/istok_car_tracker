"""Журнал карточек на настоящем Postgres: запись, пересборка дня, догоняющий снимок."""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

import pytest

psycopg = pytest.importorskip("psycopg")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import cardlog, mirror, store  # noqa: E402
from db.capture import CAPTURE_TZ  # noqa: E402

D1 = date(2026, 10, 24)
D2 = date(2026, 10, 25)
HEADER = ["Name", "telegramID", "Phone Number", "Shift",
          "Passenger1", "Passenger2", "Passenger3", "Passenger4", "site"]


class Sheet:
    """drivers_passengers, который можно править между прогонами."""

    def __init__(self, rows):
        self.rows = rows

    def _values(self, _name):
        return [HEADER, *self.rows]


@pytest.fixture
def conn(pg, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg)
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute("TRUNCATE card_log, carpool, carpool_snapshot, person, capture_run"
                  " RESTART IDENTITY CASCADE")
        yield c


def _log(c):
    return c.execute("SELECT log_date, telegram_id, passengers FROM card_log"
                     " ORDER BY id").fetchall()


def test_first_run_writes_every_card_then_only_changes(conn):
    sheet = Sheet([["Ivan Ivanov", "111", "", "Day", "Aa Aa", "Bb Bb"],
                   ["Petr Petrov", "222", "", "Night"]])
    first = cardlog.log_cards(sheet, "drivers_passengers", today=D1)
    assert first["changed"] == 2
    again = cardlog.log_cards(sheet, "drivers_passengers", today=D1)
    assert again["changed"] == 0

    sheet.rows[0] = ["Ivan Ivanov", "111", "", "Day", "Aa Aa"]
    cardlog.log_cards(sheet, "drivers_passengers", today=D2)
    assert _log(conn)[-1] == (D2, 111, ["Aa Aa"])
    assert len(_log(conn)) == 3


def test_broken_read_writes_nothing(conn):
    rows = [[f"Driver {i}", str(1000 + i), "", "Day", "Aa Aa"] for i in range(10)]
    cardlog.log_cards(Sheet(rows), "drivers_passengers", today=D1)
    info = cardlog.log_cards(Sheet([]), "drivers_passengers", today=D2)
    assert info["skipped"]
    assert all(r[0] == D1 for r in _log(conn)), "ни одной «опустевшей» карточки"


def test_rebuild_counts_passenger_removed_before_the_snapshot(conn):
    """Bb стоял утром D2, к 21:05 его убрали — день D2 ему засчитан."""
    conn.execute("INSERT INTO card_log (log_date, telegram_id, driver_name, passengers)"
                 " VALUES (%s, 111, 'Ivan Ivanov', %s)", (D1, ["Aa Aa", "Bb Bb"]))
    conn.execute("INSERT INTO card_log (log_date, telegram_id, driver_name, passengers)"
                 " VALUES (%s, 111, 'Ivan Ivanov', %s)", (D2, ["Aa Aa"]))
    conn.execute("INSERT INTO carpool_snapshot (snapshot_date, telegram_id, driver_name,"
                 " passengers, source) VALUES (%s, 111, 'Ivan Ivanov', %s, 'live')",
                 (D2, ["Aa Aa"]))

    info = mirror.rebuild_day(D2)

    names = [r[0] for r in conn.execute(
        "SELECT p.full_name FROM carpool c JOIN person p ON p.id = c.passenger_id"
        " WHERE c.ride_date = %s ORDER BY c.seat", (D2,)).fetchall()]
    assert names == ["Aa Aa", "Bb Bb"]
    assert info["written"] == 2


def test_rebuild_falls_back_to_the_log_when_the_snapshot_is_missing(conn):
    conn.execute("INSERT INTO card_log (log_date, telegram_id, driver_name, passengers)"
                 " VALUES (%s, 111, 'Ivan Ivanov', %s)", (D1, ["Aa Aa", "Bb Bb"]))
    info = mirror.rebuild_day(D2)
    assert info["written"] == 2


def test_day_cards_respects_the_range(conn):
    conn.execute("INSERT INTO card_log (log_date, telegram_id, driver_name, passengers)"
                 " VALUES (%s, 111, 'Ivan Ivanov', %s)", (D1, ["Aa Aa"]))
    with store._connect() as c, c.cursor() as cur:
        got = cardlog.day_cards(cur, start=D2, end=D2)
    assert got == [(D2, 111, "Ivan Ivanov", ["Aa Aa"])]


def _run(conn, local_hour: int, local_minute: int, *, error=None, source="live"):
    started = datetime(2026, 10, 24, local_hour, local_minute, tzinfo=CAPTURE_TZ)
    conn.execute("INSERT INTO capture_run (source, started_at, finished_at, error)"
                 " VALUES (%s, %s, %s, %s)", (source, started, started, error))


def test_evening_snapshot_found(conn):
    _run(conn, 21, 5)
    assert store.live_captured_on(D1)


def test_morning_capture_is_not_the_evening_snapshot(conn):
    _run(conn, 10, 0)
    assert not store.live_captured_on(D1)


def test_failed_capture_does_not_count(conn):
    _run(conn, 21, 5, error="boom")
    assert not store.live_captured_on(D1)
