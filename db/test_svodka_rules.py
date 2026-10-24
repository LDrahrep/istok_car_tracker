"""Порог пассажиров по объекту — на настоящем Postgres.

Сводка — это SQL, и фейковый курсор проверил бы только число подстановок,
а не то, что считается. Поэтому здесь поднимается временный кластер
(initdb во временной папке), на него накатывается schema.sql, и сводка
считается по-настоящему. Нет локального Postgres или psycopg — тесты
пропускаются, остальной набор от них не зависит.

Правило пользователя от 24.10: в Childress день засчитывается только при
трёх пассажирах, начиная с недели 19.10. Остальные объекты — два, как было.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import svodka  # noqa: E402


def _pg_bin(name: str):
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/opt/postgresql@16/bin/{name}",
                      f"/usr/local/opt/postgresql@16/bin/{name}"):
        if os.path.exists(candidate):
            return candidate
    return None


INITDB, PG_CTL = _pg_bin("initdb"), _pg_bin("pg_ctl")
pytestmark = pytest.mark.skipif(not (INITDB and PG_CTL), reason="нет локального Postgres")

RULE_DAY = date(2026, 10, 19)       # с этого понедельника в Childress нужно трое
BEFORE = date(2026, 10, 18)         # воскресенье прошлой недели — ещё по-старому
AFTER = date(2026, 10, 20)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    base = tmp_path_factory.mktemp("pg")
    data = base / "data"
    subprocess.run([INITDB, "-D", str(data), "-A", "trust", "-U", "test", "--no-sync"],
                   check=True, capture_output=True)
    port = _free_port()
    # Unix-сокет выключен: путь к временной папке на macOS длиннее лимита.
    opts = (f"-p {port} -c listen_addresses=127.0.0.1 "
            "-c unix_socket_directories='' -c fsync=off")
    subprocess.run([PG_CTL, "-D", str(data), "-o", opts, "-l", str(base / "log"),
                    "-w", "start"], check=True, capture_output=True)
    dsn = f"postgresql://test@127.0.0.1:{port}/postgres"
    schema = (Path(ROOT) / "db" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema)
    yield dsn
    subprocess.run([PG_CTL, "-D", str(data), "-m", "immediate", "stop"],
                   capture_output=True)


@pytest.fixture
def db(pg, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg)
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("TRUNCATE carpool, presence, carpool_snapshot, person,"
                     " payout_rule RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO site (id, name) VALUES ('CHILDRESS', 'Childress'),"
                     " ('TULANE', 'Tulane') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO payout_rule (site_id, valid_from, min_passengers)"
                     " VALUES ('CHILDRESS', %s, 3)", (RULE_DAY,))
        yield World(conn)


class World:
    """Люди, отметки и поездки — ровно столько, сколько нужно тесту."""

    def __init__(self, conn):
        self.conn = conn

    def person(self, name: str, site: str | None = None) -> int:
        row = self.conn.execute(
            "INSERT INTO person (full_name, name_key, current_site_id)"
            " VALUES (%s, %s, %s) RETURNING id",
            (name, " ".join(sorted(name.lower().split())), site)).fetchone()
        return row[0]

    def present(self, pid: int, day: date, site: str) -> None:
        self.conn.execute(
            "INSERT INTO presence (person_id, work_date, site_id, source)"
            " VALUES (%s, %s, %s, 'test')", (pid, day, site))

    def ride(self, day: date, driver: int, passengers: list[int]) -> None:
        for seat, pid in enumerate(passengers, start=1):
            self.conn.execute(
                "INSERT INTO carpool (ride_date, driver_id, passenger_id, seat, source)"
                " VALUES (%s, %s, %s, %s, 'snapshot')", (day, driver, pid, seat))

    def car(self, site: str, day: date, n_passengers: int, *, prefix: str,
            driver_present: bool = True) -> tuple[int, list[int]]:
        driver = self.person(f"Driver {prefix}", site)
        pax = [self.person(f"Pax {prefix}{i}", site) for i in range(n_passengers)]
        if driver_present:
            self.present(driver, day, site)
        self.ride(day, driver, pax)
        return driver, pax


def _days(info: dict, site: str, name: str) -> str:
    table = info["tables"].get(site, [])
    for row in table[1:]:
        if row[0] == name:
            return row[1]
    return ""


# ───────────────────────────── строгое правило ─────────────────────────────

def test_childress_two_passengers_are_not_enough_after_the_rule(db):
    db.car("CHILDRESS", AFTER, 2, prefix="c2")
    info = svodka.build(AFTER, AFTER, mode="strict")
    assert _days(info, "CHILDRESS", "Driver c2") == ""


def test_childress_three_passengers_count(db):
    db.car("CHILDRESS", AFTER, 3, prefix="c3")
    info = svodka.build(AFTER, AFTER, mode="strict")
    assert _days(info, "CHILDRESS", "Driver c3") == "1"


def test_childress_two_passengers_still_count_before_the_rule(db):
    """Правило со дня, а не задним числом: пересчёт прошлой недели не меняется."""
    db.car("CHILDRESS", BEFORE, 2, prefix="old")
    info = svodka.build(BEFORE, BEFORE, mode="strict")
    assert _days(info, "CHILDRESS", "Driver old") == "1"


def test_other_sites_keep_two_passengers(db):
    db.car("TULANE", AFTER, 2, prefix="t2")
    info = svodka.build(AFTER, AFTER, mode="strict")
    assert _days(info, "TULANE", "Driver t2") == "1"


def test_threshold_follows_the_site_of_the_day_not_the_home_site(db):
    """Объект дня — из присутствия: тюлейновец, отмеченный в Childress, по правилу Childress."""
    driver = db.person("Driver moved", "TULANE")
    pax = [db.person(f"Pax moved{i}", "TULANE") for i in range(2)]
    db.present(driver, AFTER, "CHILDRESS")
    db.ride(AFTER, driver, pax)
    info = svodka.build(AFTER, AFTER, mode="strict")
    assert _days(info, "CHILDRESS", "Driver moved") == ""


# ──────────────────────────── справедливое правило ───────────────────────────

def test_fair_childress_recorded_two_is_not_enough(db):
    db.car("CHILDRESS", AFTER, 2, prefix="f2")
    info = svodka.build(AFTER, AFTER, mode="fair")
    assert _days(info, "CHILDRESS", "Driver f2") == ""


def test_fair_disputed_childress_day_needs_three_free_passengers_at_work(db):
    """Спорный день: записи нет, но постоянные пассажиры на работе.
    В Childress их должно быть трое, а не двое."""
    two = date(2026, 10, 21)
    three = date(2026, 10, 22)
    driver, pax = db.car("CHILDRESS", AFTER, 3, prefix="pool")
    for day, at_work in ((two, pax[:2]), (three, pax)):
        db.present(driver, day, "CHILDRESS")
        for p in at_work:
            db.present(p, day, "CHILDRESS")
    info = svodka.build(AFTER, three, mode="fair")
    # 20.10 записан с тремя, 22.10 спорный и трое на работе; 21.10 — только двое.
    assert _days(info, "CHILDRESS", "Driver pool") == "2"


def test_fair_disputed_tulane_day_still_needs_two(db):
    driver, pax = db.car("TULANE", AFTER, 2, prefix="tp")
    day = date(2026, 10, 21)
    db.present(driver, day, "TULANE")
    for p in pax:
        db.present(p, day, "TULANE")
    info = svodka.build(AFTER, day, mode="fair")
    assert _days(info, "TULANE", "Driver tp") == "2"


# ─────────────────────── колонки «возил», табель, без отметки ───────────────────────

def test_drove_column_counts_only_days_that_meet_the_site_rule(db):
    driver, pax = db.car("CHILDRESS", AFTER, 2, prefix="dr")
    day = date(2026, 10, 21)
    extra = db.person("Pax dr2", "CHILDRESS")
    db.present(driver, day, "CHILDRESS")
    db.ride(day, driver, pax + [extra])
    info = svodka.build(AFTER, day, mode="strict")
    row = next(r for r in info["tables"]["CHILDRESS"][1:] if r[0] == "Driver dr")
    drove = row[-4]
    assert drove == "1"


def test_timesheet_rule_needs_one_qualifying_ride_per_site(db):
    """Правило GAS: день за отметку, если в периоде была хоть одна поездка,
    проходящая порог объекта."""
    db.car("CHILDRESS", AFTER, 2, prefix="ts")
    info = svodka.build(AFTER, AFTER, mode="timesheet")
    assert _days(info, "CHILDRESS", "Driver ts") == ""


def test_unmarked_list_uses_the_home_site_rule(db):
    """Без отметки объекта дня нет — порог берётся по текущему объекту человека."""
    db.car("CHILDRESS", AFTER, 2, prefix="um2", driver_present=False)
    db.car("CHILDRESS", AFTER, 3, prefix="um3", driver_present=False)
    info = svodka.build(AFTER, AFTER, mode="strict")
    names = [name for name, _ in info["unmarked"]]
    assert "Driver um3" in names
    assert "Driver um2" not in names
