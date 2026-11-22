"""Тесты переноса «правок» на фейковом соединении (Postgres и Google недоступны)."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import fixes_import as fi  # noqa: E402
from db.fixes import DbFix, SheetRow, plan_import  # noqa: E402

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
CHI = ZoneInfo("America/Chicago")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.conn.rows

    def fetchone(self):
        sql, params = self.conn.log[-1]
        if sql.startswith("SELECT paid_at"):
            return (self.conn.paid_at.get(params[0]),)
        return None


class FakeConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.log: list[tuple[str, tuple | None]] = []
        self.commits = 0
        self.paid_at: dict[int, datetime] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def writes(self):
        return [(s, p) for s, p in self.log if s.startswith(("INSERT", "UPDATE"))]


def fake_sheet() -> list[SheetRow]:
    """53 строки: 1–25 и 26–41 зелёные (выдуманные имена), 42–53 по сиду, белые."""
    rows = [SheetRow(i, f"Driver{i} Test", "10/12-10/25", "14 дней", True)
            for i in range(1, 42)]
    rows += [SheetRow(n, name, p, d, False) for n, name, p, d, *_ in fi.FIRST_IMPORT]
    return rows


def patch_main(monkeypatch, sheet, conn, db=()):
    monkeypatch.setattr(fi, "read_sheet", lambda client: sheet)
    monkeypatch.setattr(fi, "read_db", lambda c: list(db))
    monkeypatch.setattr(fi, "_client", lambda: None)
    monkeypatch.setattr(fi, "_connect", lambda: conn)


def test_a_dry_run_writes_nothing(monkeypatch):
    conn = FakeConn()
    patch_main(monkeypatch, [SheetRow(1, "Ann Test", "11/09", "1 день", False)], conn)
    assert fi.main([]) == 0
    assert conn.writes() == [] and conn.commits == 0


def test_apply_inserts_new_rows_with_import_dates_and_logs_the_counts():
    plan = plan_import([SheetRow(1, "Ann Test", "11/09", "1 день", True),
                        SheetRow(2, "Bob Test", "11/10", "2 дня", False)], [])
    conn = FakeConn()
    counts = fi.apply_plan(conn, plan, NOW)
    assert counts["new"] == 2 and conn.commits == 1
    ins = [p for s, p in conn.writes() if s.startswith("INSERT INTO payout_fix")]
    sql = [s for s, _ in conn.writes()][0]
    assert "SELECT id FROM person WHERE name_key = %s" in sql
    green, white = ins
    # driver_name, key, key, period, start, end, days_text, days, row, added_at, how, paid, paid_at, imported_at
    assert green[9] == NOW and green[10] == "import" and green[11] is True
    assert green[12] == NOW and green[13] == NOW and green[7] == 1
    assert white[11] is False and white[12] is None
    last_sql, last_p = conn.log[-1]
    assert last_sql.startswith("INSERT INTO audit_log") and "'admin:claude'" in last_sql
    assert "'fixes_import'" in last_sql
    d = json.loads(last_p[0])
    assert {k: d[k] for k in (*counts, "first")} == {**counts, "first": False}


def test_paid_unpaid_removed_returned_changed_are_updates_that_keep_added_and_created_at():
    k = fi.name_key("Ann Test")
    db = [DbFix(1, k, "11/09", "1 день", 1, False, False),   # оплатят
          DbFix(2, k, "11/10", "1 день", 2, True, False),    # снимут оплату
          DbFix(3, k, "11/11", "1 день", 3, False, False),   # снята
          DbFix(4, k, "11/12", "1 день", 4, False, True),    # вернулась
          DbFix(5, k, "11/13", "1 день", 5, False, False)]   # изменена (дни)
    sheet = [SheetRow(1, "Ann Test", "11/09", "1 день", True),
             SheetRow(2, "Ann Test", "11/10", "1 день", False),
             SheetRow(4, "Ann Test", "11/12", "1 день", False),
             SheetRow(5, "Ann Test", "11/13", "2 дня", False)]
    plan = plan_import(sheet, db)
    conn = FakeConn()
    counts = fi.apply_plan(conn, plan, NOW)
    assert counts == {"new": 0, "changed": 1, "paid": 1, "unpaid": 1,
                      "removed": 1, "returned": 1}
    ups = [s for s, _ in conn.writes() if not s.startswith("INSERT INTO audit_log")]
    assert ups
    for s in ups:
        assert s.startswith("UPDATE payout_fix")
        assert s.split(" WHERE ")[0].count("added_at") == 0
        assert "created_at" not in s
        set_part = s.split(" WHERE ")[0].replace("(SELECT id FROM person", "")
        assert " id =" not in set_part
    assert any("paid = true" in s for s in ups) and any("paid = false" in s for s in ups)
    assert any("removed_at = NULL" in s for s in ups)
    assert any("removed_at = %s" in s for s in ups)


def test_the_first_import_stops_unless_rows_42_to_53_are_as_in_the_seed(monkeypatch):
    conn = FakeConn()
    short = fake_sheet()[:-1]
    patch_main(monkeypatch, short, conn)
    with pytest.raises(SystemExit, match="53"):
        fi.main(["--first", "--apply"])
    bad = fake_sheet()
    bad[49] = SheetRow(50, "Orif Alimov", "10/26-11/08", "13 дней", False)
    patch_main(monkeypatch, bad, conn)
    with pytest.raises(SystemExit, match="строка 50"):
        fi.main(["--first", "--apply"])
    assert conn.writes() == []


def test_the_first_import_restores_the_seeded_dates(monkeypatch):
    conn = FakeConn()
    patch_main(monkeypatch, fake_sheet(), conn)
    assert fi.main(["--first", "--apply"]) == 0
    ins = {p[8]: p for s, p in conn.writes() if s.startswith("INSERT INTO payout_fix")}
    assert len(ins) == 53
    r50 = ins[50]
    assert r50[9] == datetime(2026, 11, 17, tzinfo=CHI) and r50[10] == "restored"
    assert r50[11] is False and r50[12] is None
    assert ins[30][9] == datetime(2026, 11, 9, tzinfo=CHI)
    assert ins[30][11] is True and ins[30][12] == datetime(2026, 11, 9, tzinfo=CHI)
    assert ins[10][9] is None and ins[10][10] == "unknown"
    assert ins[10][11] is True and ins[10][12] is None
    audit = json.loads(conn.log[-1][1][0])
    assert audit["first"] is True and audit["new"] == 53
    assert conn.commits == 1


def test_the_first_import_refuses_a_non_empty_table(monkeypatch):
    conn = FakeConn()
    patch_main(monkeypatch, fake_sheet(), conn,
               db=[DbFix(1, "a", "p", "d", 1, False, False)])
    with pytest.raises(SystemExit, match="уже 1"):
        fi.main(["--first", "--apply"])
    assert conn.writes() == []


def test_unpaid_keeps_the_old_paid_at_and_the_ids_in_the_audit_details():
    k = fi.name_key("Ann Test")
    db = [DbFix(2, k, "11/10", "1 день", 2, True, False),
          DbFix(3, k, "11/11", "1 день", 3, False, False),
          DbFix(5, k, "11/13", "1 день", 5, False, False)]
    sheet = [SheetRow(2, "Ann Test", "11/10", "1 день", False),
             SheetRow(5, "Ann Test", "11/13", "2 дня", False)]
    conn = FakeConn()
    conn.paid_at[2] = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    fi.apply_plan(conn, plan_import(sheet, db), NOW)
    sqls = [s for s, _ in conn.log]
    # дата читается раньше, чем обнуляется
    assert sqls.index("SELECT paid_at FROM payout_fix WHERE id = %s") < \
        sqls.index("UPDATE payout_fix SET paid = false, paid_at = NULL WHERE id = %s")
    d = json.loads(conn.log[-1][1][0])
    assert d["unpaid_ids"] == [{"id": 2, "paid_at": "2026-11-09T12:00:00+00:00"}]
    assert d["removed_ids"] == [3]
    assert d["changed_ids"] == [{"id": 5, "was": [k, "11/13", "1 день"]}]
    assert d["returned_ids"] == []
