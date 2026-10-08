"""Тесты записи импорта без Postgres.

Postgres живёт внутри Railway и локально недоступен, поэтому соединение
подменяется фейком, который ведёт себя как таблицы с ON CONFLICT: хранит
строки по ключу и отвечает на count(*). Это ловит именно те ошибки, которые
иначе обнаружились бы только на первом реальном прогоне, — рассогласование
числа подстановок с SQL, неверный порядок вставок относительно внешних
ключей, имя вместо person_id в presence.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import roster  # noqa: E402
from db.importer import name_key  # noqa: E402

from datetime import date  # noqa: E402

HEADER = ["", "", "09/14/2026", "09/15/2026", "", "", "", "", ""]
WEEKDAYS = ["", "Name", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

BUFFALO = "09142026-09202026 BUFFALO"
AMAZON = "8242026-8302026 AMAZON"

# Ключ строки в фейковой таблице = то, по чему в схеме объявлен конфликт.
KEY = {
    "site": lambda r: r[0],
    "person": lambda r: r[1],
    "presence": lambda r: (r[0], r[1], r[2]),
    # Тот же ключ, что в схеме: пассажир у одного водителя в день.
    "carpool": lambda r: (r[0], r[2]),
}

# Снапшоты, из которых собираются связи. Aldar везёт Badma.
SNAPSHOTS = [
    (date(2026, 10, 1), 111, "Aldar Rakshaev", ["Badma Matsakov"]),
    (date(2026, 10, 1), 999, "Неизвестный Водитель", ["Badma Matsakov"]),
]


class FakeDB:
    def __init__(self):
        self.tables = {"site": {}, "person": {}, "presence": {}, "carpool": {}}
        self.log: list[tuple[str, str, object]] = []

    def person_ids(self) -> dict:
        """name_key → id, в том же порядке, в каком их отдаёт фейковый SELECT."""
        return {k: i + 1 for i, k in enumerate(self.tables["person"])}

    def steps(self) -> list[str]:
        """Последовательность операций в сокращённом виде — для проверки порядка."""
        out = []
        for kind, sql, _ in self.log:
            if kind == "executemany":
                out.append("many:" + sql.split()[2])
            elif sql.startswith("UPDATE person SET telegram_id = NULL"):
                out.append("release")
            elif sql.startswith("SELECT name_key, id"):
                out.append("read_ids")
            elif sql.startswith("DELETE FROM presence"):
                out.append("clear_presence")
            elif sql.startswith("DELETE FROM carpool"):
                out.append("clear_carpool")
            elif sql.startswith("UPDATE person p SET full_name"):
                out.append("rename")
            elif sql.startswith("INSERT INTO audit_log"):
                out.append("audit")
        return out


class FakeCursor:
    def __init__(self, db: FakeDB):
        self.db = db
        self._one = None
        self._all = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.db.log.append(("execute", s, params))
        if s.startswith("INSERT INTO capture_run"):
            self._one = (1,)
        elif s.startswith("SELECT count(*) FROM "):
            self._one = (len(self.db.tables[s.split("FROM ")[1].strip()]),)
        elif s.startswith("SELECT id, aliases FROM site"):
            # Справочник объектов приезжает из базы; на фейке отдаём канон.
            from db.importer import SITE_ALIASES
            byid = {}
            for alias, sid in SITE_ALIASES.items():
                byid.setdefault(sid, []).append(alias)
            self._all = sorted(byid.items())
        elif s.startswith("SELECT name_key, id FROM person"):
            self._all = list(self.db.person_ids().items())
        elif s.startswith("SELECT telegram_id, id FROM person"):
            ids = self.db.person_ids()
            self._all = [(r[3], ids[k])
                         for k, r in self.db.tables["person"].items()
                         if r[3] is not None]
        elif s.startswith("SELECT snapshot_date, telegram_id"):
            self._all = list(SNAPSHOTS)
        elif s.startswith("DELETE FROM presence"):
            # Лист перезаписывает свой диапазон: чистим то, что он покрывает.
            sites, days = params
            covered = set(zip(sites, days))
            for k in [k for k, r in self.db.tables["presence"].items()
                      if (r[2], r[1]) in covered]:
                del self.db.tables["presence"][k]
        elif s.startswith("DELETE FROM carpool"):
            for k in [k for k, r in self.db.tables["carpool"].items()
                      if r[-1] == "snapshot"]:
                del self.db.tables["carpool"][k]
        elif s.startswith("UPDATE person p SET full_name"):
            # Переименование по telegram_id. На фейке результат не моделируем —
            # SQL проверен на живой базе; здесь важен только факт и порядок.
            self.rowcount = 0
        elif s.startswith("UPDATE person SET telegram_id = NULL"):
            pass
        elif s.startswith("UPDATE capture_run"):
            pass
        elif s.startswith("INSERT INTO audit_log"):
            self.db.log.append(("audit", s, params))
        else:
            raise AssertionError(f"неожиданный SQL: {s[:90]}")

    def executemany(self, sql, seq):
        s = " ".join(sql.split())
        seq = list(seq)
        self.db.log.append(("executemany", s, seq))
        table = s.split()[2]
        assert table in self.db.tables, f"неизвестная таблица: {table}"
        for row in seq:
            stored = row + ("snapshot",) if table == "carpool" else row
            self.db.tables[table][KEY[table](row)] = stored

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def __init__(self, db: FakeDB):
        self.db = db
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class FakeSheets:
    """Минимальный SheetManager: только то, что трогает импорт."""

    def __init__(self):
        self.read: list[str] = []

    def sheet_titles(self):
        return [BUFFALO, AMAZON, "Svodka Buffalo", "Svodka Columbus",
                "employees", "drivers", "PHASE 5 AMAZON", "week2"]

    def _values(self, name):
        self.read.append(name)
        if name == "drivers":
            # Один и тот же ID на двух строках — водителя переименовали,
            # старая строка осталась. Реальный источник конфликта UNIQUE.
            return [["Name", "telegramID", "isActive"],
                    ["Aldar Rakshaev", "111", "TRUE"],
                    ["Aldar Rakshaeff", "111", "FALSE"]]
        if "BUFFALO" in name:
            return [HEADER, WEEKDAYS,
                    ["1", "Badma Matsakov", "8.0", "10.0", "", "", "", "", ""]]
        if "AMAZON" in name:
            return [HEADER, WEEKDAYS,
                    ["1", "Aldar Rakshaev", "", "8.0", "", "", "", "", ""]]
        return []

    def get_all_employees(self):
        return [SimpleNamespace(name="Aldar Rakshaev", shift="Day"),
                SimpleNamespace(name="Aldar Rakshaeff", shift="Day"),
                SimpleNamespace(name="Badma Matsakov", shift="Meltech Night")]


def run(only=None):
    db = FakeDB()
    sheets = FakeSheets()
    roster._connect = lambda: FakeConn(db)
    roster.enabled = lambda: True
    info = roster.import_roster(sheets, drivers_sheet="drivers", only=only)
    return info, db, sheets


# ───────────────────────── порядок и форма записи ─────────────────────────

def test_sites_written_before_rows_that_reference_them():
    """person.current_site_id и presence.site_id — REFERENCES site(id).

    Вставка людей раньше справочника объектов упала бы по внешнему ключу,
    причём на реальной базе, а не в тесте.
    """
    _, db, _ = run()
    steps = db.steps()
    assert steps.index("many:site") < steps.index("many:person")
    assert steps.index("many:site") < steps.index("many:presence")


def test_telegram_ids_released_before_person_upsert():
    """Снять ID со старого имени нужно ДО вставки, иначе UNIQUE уронит пачку."""
    _, db, _ = run()
    steps = db.steps()
    assert steps.index("release") < steps.index("many:person")


def test_person_ids_read_after_upsert_before_presence():
    """person_id известен только после вставки людей."""
    _, db, _ = run()
    steps = db.steps()
    assert steps.index("many:person") < steps.index("read_ids") < steps.index("many:presence")


def test_param_counts_match_sql_placeholders():
    """Рассинхрон числа подстановок — отказ на первом же реальном прогоне."""
    _, db, _ = run()
    expected = {"site": 2, "person": 5, "presence": 5, "carpool": 4}
    for kind, sql, seq in db.log:
        if kind != "executemany":
            continue
        table = sql.split()[2]
        assert sql.count("%s") == expected[table], f"{table}: SQL"
        for row in seq:
            assert len(row) == expected[table], f"{table}: строка {row}"


def test_presence_carries_person_id_not_name():
    _, db, _ = run()
    ids = set(range(1, len(db.tables["person"]) + 1))
    for person_id, _work_date, _site, source, _hours in db.tables["presence"].values():
        assert person_id in ids, f"presence ссылается на {person_id!r}"
        assert source == "timesheet"


# ───────────────────────── что читается из таблицы ─────────────────────────

def test_only_timesheets_are_read():
    """Лишний лист = лишний запрос к Google, а квота почти выбрана рассылкой."""
    _, _, sheets = run()
    assert set(sheets.read) == {BUFFALO, AMAZON, "drivers"}


def test_only_filter_narrows_the_run():
    info, _, sheets = run(only="buffalo")
    assert info["sheets"] == 1
    assert AMAZON not in sheets.read


# ───────────────────────── содержимое и статистика ─────────────────────────

def test_site_catalog_is_complete_regardless_of_this_week():
    """Справочник объектов не зависит от того, кто где отметился.

    В этом прогоне отметки есть только по BUFFALO и AMAZON, но COLUMBUS и
    MELTECH обязаны быть в таблице: иначе в админке не выбрать объект, где
    сейчас никого нет, и перевести туда человека нечем.
    """
    _, db, _ = run()
    assert set(db.tables["site"]) == {"AMAZON", "BUFFALO", "COLUMBUS", "MELTECH"}
    assert db.tables["site"]["AMAZON"][1] == "Amazon", "имя объекта для человека"


def test_duplicate_telegram_id_is_reported_and_only_one_keeps_it():
    info, db, _ = run()
    assert len(info["tg_conflicts"]) == 1
    owner, loser, tg = info["tg_conflicts"][0]
    assert tg == 111
    assert {owner, loser} == {name_key("Aldar Rakshaev"), name_key("Aldar Rakshaeff")}

    with_tg = [r for r in db.tables["person"].values() if r[3] is not None]
    assert len(with_tg) == 1, "UNIQUE допускает только одного владельца ID"


def test_current_site_follows_the_timesheet():
    """У Badma смена «Meltech Night», но отметки в BUFFALO — объект берётся оттуда."""
    _, db, _ = run()
    row = db.tables["person"][name_key("Badma Matsakov")]
    assert row[2] == "night", "смена из employees"
    assert row[4] == "BUFFALO", "объект из последней отметки"


def test_statistics_reflect_what_was_written():
    info, db, _ = run()
    assert info["people_new"] == len(db.tables["person"]) == 3
    assert info["presence_new"] == len(db.tables["presence"]) == 3
    assert info["presence_read"] == 3
    assert info["orphans"] == 0
    assert info["sites_new"] == 4


# ───────────────────────── связи водитель↔пассажир ─────────────────────────

def test_carpool_built_after_people_exist():
    """driver_id и passenger_id — REFERENCES person(id).

    Собирать связи раньше, чем появились люди, бессмысленно: сопоставлять
    имена будет не с чем.
    """
    _, db, _ = run()
    steps = db.steps()
    assert steps.index("many:person") < steps.index("clear_carpool")
    assert steps.index("clear_carpool") < steps.index("many:carpool")


def test_carpool_links_real_person_ids():
    _, db, _ = run()
    ids = db.person_ids()
    assert len(db.tables["carpool"]) == 1
    (ride_date, driver_id, passenger_id, seat, source), = db.tables["carpool"].values()
    assert driver_id == ids[name_key("Aldar Rakshaev")]
    assert passenger_id == ids[name_key("Badma Matsakov")]
    assert seat == 1 and source == "snapshot"


def test_carpool_reports_what_did_not_link():
    """Второй снапшот от неизвестного водителя не должен исчезнуть молча."""
    info, _, _ = run()
    assert info["carpool_built"] == 1
    assert len(info["carpool_problems"]["driver"]) == 1


def test_carpool_rebuild_is_idempotent():
    """Импорт запускают повторно — связи не должны множиться."""
    info_a, db_a, _ = run()
    info_b, db_b, _ = run()
    assert info_a["carpool_total"] == info_b["carpool_total"] == 1
    assert len(db_b.tables["carpool"]) == 1


def test_rename_runs_before_releasing_telegram_ids():
    """Порядок критичен.

    RELEASE_TGIDS снимает telegram_id со всех записей, чьё имя не пришло
    в этом прогоне. Если он отработает раньше переименования, у старого
    имени ID уже не будет — и связать переименованного человека с его
    историей станет нечем.
    """
    _, db, _ = run()
    steps = db.steps()
    assert steps.index("rename") < steps.index("release")


def test_import_writes_an_audit_record():
    """Журнал для того и заведён: «что изменилось с прошлого раза»
    иначе восстанавливается только по памяти."""
    _, db, _ = run()
    assert "audit" in db.steps()


def test_sheet_range_is_cleared_before_writing():
    """Исчезнувшая из табеля отметка должна исчезнуть и из базы.

    Импорт только добавлял: убрали человеку день — а база продолжала его
    засчитывать, и выглядело это нормально, потому что строка «была
    и осталась». Четыре такие нашлись в живых данных.
    """
    _, db, _ = run()
    steps = db.steps()
    assert "clear_presence" in steps
    assert steps.index("clear_presence") < steps.index("many:presence")
