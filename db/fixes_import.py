"""Перенос листа «правки» (ВОДИЛЫ) в таблицу payout_fix. Запуск вручную.

    python -m db.fixes_import [--first] [--apply]

Без --apply только печатает, что поменяется, и ничего не пишет. С --apply
делает всё в одной транзакции и пишет итог в audit_log (fixes_import).
Лист только читается (значения и заливка), в нём ничего не правится.
--first — первый перенос: даты добавления и оплаты восстанавливаются по
списку FIRST_IMPORT и правилам для строк 1–41 (см. дизайн 2026-11-22-fixes).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from db.fixes import (FIXES_TAB, VODILY_ID, DbFix, ImportPlan, SheetRow,
                      parse_days, parse_period, plan_import, rows_from_sheet)
from db.importer import name_key

CHICAGO = ZoneInfo("America/Chicago")

_D9 = date(2026, 11, 9)
_D13 = date(2026, 11, 13)
_D17 = date(2026, 11, 17)
_D21 = date(2026, 11, 21)

# (строка листа, имя, период, дней, добавлено, оплачено, дата оплаты) — строки 42–53 на 22.11.
FIRST_IMPORT: list[tuple[int, str, str, str, date | None, bool, date | None]] = [
    (42, "Inom Khujanazarov", "11/03-11/07", "5 дней", _D13, False, None),
    (43, "Zhyrgalbek Aitmamat uulu", "10/18", "1 день", _D13, False, None),
    (44, "Zhyrgalbek Aitmamat uulu", "10/19-10/25", "7 дней", _D13, False, None),
    (45, "Zhyrgalbek Aitmamat uulu", "10/26-11/01", "7 дней", _D13, False, None),
    (46, "Zhyrgalbek Aitmamat uulu", "11/02-11/08", "7 дней", _D13, False, None),
    (47, "Sobirdzhon Dzhuraev", "11/03-11/07", "5 дней", _D13, False, None),
    (48, "Sergey Mokshantsev", "11/01", "1 день", _D13, False, None),
    (49, "Gulnara Aleksandrova", "11/13", "1 день", _D13, False, None),
    (50, "Orif Alimov", "10/26-11/08", "14 дней", _D17, False, None),
    (51, "Vadzim Papko", "10/31", "1 день", _D21, False, None),
    (52, "Sarvinoz Mukhtorova", "10/18", "1 день", _D21, False, None),
    (53, "Sarvinoz Mukhtorova", "10/19-10/22", "4 дня", _D21, False, None),
]
FIRST_ROWS = 53


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=CHICAGO)


def check_first(sheet: list[SheetRow]) -> None:
    """Первый перенос идёт только по листу, который выглядит как 22.11: 53 строки, 42–53 как в сиде."""
    if len(sheet) != FIRST_ROWS:
        raise SystemExit(f"Первый перенос: в листе {len(sheet)} строк, ожидалось {FIRST_ROWS}. "
                         "Лист изменился, сид не применяю.")
    by_row = {r.row: r for r in sheet}
    for n, name, period, days, *_ in FIRST_IMPORT:
        r = by_row.get(n)
        if (r is None or r.key != name_key(name) or r.period != period
                or r.days_text != days):
            got = f"{r.name} · {r.period} · {r.days_text}" if r else "нет строки"
            raise SystemExit(f"Первый перенос: строка {n} не совпадает с сидом "
                             f"({name} · {period} · {days}); в листе: {got}. Ничего не записано.")


def seed_dates(row: SheetRow, now: datetime, first: bool):
    """(added_at, added_how, paid_at) для новой строки.

    Обычный перенос: момент переноса. Первый: восстановленные даты — 42–53
    по сиду, 26–41 внесены и оплачены 09.11, 1–25 неизвестно (paid_at null).
    """
    paid_now = now if row.green else None
    if not first:
        return now, "import", paid_now
    for n, _, _, _, added, paid, paid_on in FIRST_IMPORT:
        if n == row.row:
            return (_midnight(added) if added else None,
                    "restored" if added else "unknown",
                    _midnight(paid_on) if paid_on else paid_now)
    if 26 <= row.row <= 41:
        return _midnight(_D9), "restored", _midnight(_D9) if row.green else None
    return None, "unknown", None


# ---- чтение -----------------------------------------------------------------

def read_sheet(client) -> list[SheetRow]:
    """Лист «правки»: значения A:C и фон колонки A. Только чтение."""
    ss = client.open_by_key(VODILY_ID)
    values = ss.worksheet(FIXES_TAB).get("A:C")
    meta = ss.fetch_sheet_metadata({
        "ranges": [f"'{FIXES_TAB}'!A:A"],
        "fields": "sheets(data(rowData(values(effectiveFormat(backgroundColor)))))",
    })
    row_data = (((meta.get("sheets") or [{}])[0].get("data") or [{}])[0]
                .get("rowData") or [])
    colors = []
    for rd in row_data:
        cells = rd.get("values") or [{}]
        colors.append(((cells[0] or {}).get("effectiveFormat") or {})
                      .get("backgroundColor") or {})
    return rows_from_sheet(values, colors)


def read_db(conn) -> list[DbFix]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, driver_key, period, days_text, sheet_row, paid,"
                    " removed_at IS NOT NULL FROM payout_fix ORDER BY id")
        return [DbFix(*r) for r in cur.fetchall()]


# ---- запись -----------------------------------------------------------------

_INSERT = (
    "INSERT INTO payout_fix (driver_name, driver_key, person_id, period,"
    " period_start, period_end, days_text, days, sheet_row, added_at, added_how,"
    " paid, paid_at, imported_at)"
    " VALUES (%s, %s, (SELECT id FROM person WHERE name_key = %s), %s,"
    " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
# Поля, которые переписываем у «изменённой»; added_at, created_at и id не трогаем.
_UPDATE_CHANGED = (
    "UPDATE payout_fix SET driver_name = %s, driver_key = %s,"
    " person_id = (SELECT id FROM person WHERE name_key = %s), period = %s,"
    " period_start = %s, period_end = %s, days_text = %s, days = %s,"
    " sheet_row = %s, imported_at = %s WHERE id = %s"
)
_UPDATE_SEEN = "UPDATE payout_fix SET sheet_row = %s, imported_at = %s WHERE id = %s"
_UPDATE_PAID = "UPDATE payout_fix SET paid = true, paid_at = %s WHERE id = %s"
_SELECT_PAID_AT = "SELECT paid_at FROM payout_fix WHERE id = %s"
_UPDATE_UNPAID = "UPDATE payout_fix SET paid = false, paid_at = NULL WHERE id = %s"
_UPDATE_REMOVED = ("UPDATE payout_fix SET removed_at = %s WHERE id = %s"
                   " AND removed_at IS NULL")
_UPDATE_RETURNED = ("UPDATE payout_fix SET removed_at = NULL, sheet_row = %s,"
                    " imported_at = %s WHERE id = %s")
_AUDIT = ("INSERT INTO audit_log (actor, action, subject, details)"
          " VALUES ('admin:claude', 'fixes_import', 'правки', %s::jsonb)")


def apply_plan(conn, plan: ImportPlan, now: datetime, first: bool = False) -> dict:
    """Применяет план одной транзакцией; возвращает счётчики."""
    today = now.date()
    try:
        with conn.cursor() as cur:
            for r in plan.new:
                added_at, how, paid_at = seed_dates(r, now, first)
                near = added_at.date() if added_at else today
                start, end = parse_period(r.period, near)
                cur.execute(_INSERT, (r.name, r.key, r.key, r.period, start, end,
                                      r.days_text, parse_days(r.days_text), r.row,
                                      added_at, how, r.green, paid_at, now))
            for f, r in plan.changed:
                start, end = parse_period(r.period, today)
                cur.execute(_UPDATE_CHANGED, (r.name, r.key, r.key, r.period, start,
                                              end, r.days_text, parse_days(r.days_text),
                                              r.row, now, f.id))
            for f, r in plan.seen:
                cur.execute(_UPDATE_SEEN, (r.row, now, f.id))
            for f, r in plan.returned:
                cur.execute(_UPDATE_RETURNED, (r.row, now, f.id))
            for f, r in plan.paid:
                cur.execute(_UPDATE_PAID, (now, f.id))
            unpaid_old = []
            for f, r in plan.unpaid:
                # прежнюю дату оплаты читаем до обнуления и кладём в журнал
                cur.execute(_SELECT_PAID_AT, (f.id,))
                row = cur.fetchone()
                old = row[0] if row else None
                unpaid_old.append({"id": f.id, "paid_at": old.isoformat() if old else None})
                cur.execute(_UPDATE_UNPAID, (f.id,))
            for f in plan.removed:
                cur.execute(_UPDATE_REMOVED, (now, f.id))
            counts = {"new": len(plan.new), "changed": len(plan.changed),
                      "paid": len(plan.paid), "unpaid": len(plan.unpaid),
                      "removed": len(plan.removed), "returned": len(plan.returned)}
            details = {
                **counts, "first": first,
                # id затронутых записей; у изменённых и с снятой оплатой ещё то, что было
                "changed_ids": [{"id": f.id, "was": [f.driver_key, f.period, f.days_text]}
                                for f, _ in plan.changed],
                "removed_ids": [f.id for f in plan.removed],
                "unpaid_ids": unpaid_old,
                "returned_ids": [f.id for f, _ in plan.returned],
            }
            cur.execute(_AUDIT, (json.dumps(details, ensure_ascii=False),))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


# ---- вывод и запуск ---------------------------------------------------------

def _line(r: SheetRow) -> str:
    return f"строка {r.row}: {r.name} · {r.period} · {r.days_text}"


def format_plan(plan: ImportPlan) -> str:
    out = []

    def block(title, items):
        out.append(f"{title}: {len(items)}")
        out.extend(f"  {i}" for i in items)

    block("Новые", [_line(r) for r in plan.new])
    block("Изменены", [f"id {f.id}: {f.driver_key} · {f.period} · {f.days_text}"
                       f" -> {_line(r)}" for f, r in plan.changed])
    block("Оплачены", [f"id {f.id}, {_line(r)}" for f, r in plan.paid])
    block("СНЯТА ОПЛАТА (бывает только по ошибке)",
          [f"id {f.id}, {_line(r)}" for f, r in plan.unpaid])
    block("Сняты (нет в листе)", [f"id {f.id}: {f.driver_key} · {f.period} · {f.days_text}"
                                  for f in plan.removed])
    block("Вернулись", [f"id {f.id}, {_line(r)}" for f, r in plan.returned])
    out.append(f"Без изменений: {len(plan.seen)}")
    return "\n".join(out)


def _client():
    from config import Config
    from sheets import SheetManager
    return SheetManager(Config()).client


def _connect():
    from db.store import _connect as connect
    return connect()


def main(argv: list[str]) -> int:
    unknown = [a for a in argv if a not in ("--apply", "--first")]
    if unknown:
        print(f"Неизвестные параметры: {' '.join(unknown)}. Допустимы --first и --apply.")
        return 2
    apply, first = "--apply" in argv, "--first" in argv
    now = datetime.now(timezone.utc)
    sheet = read_sheet(_client())
    if first:
        check_first(sheet)
    with _connect() as conn:
        db = read_db(conn)
        if first and db:
            raise SystemExit(f"Первый перенос: в payout_fix уже {len(db)} строк. Ничего не записано.")
        plan = plan_import(sheet, db)
        print(format_plan(plan))
        if not apply:
            print("\nПробный прогон: ничего не записано. Для записи добавьте --apply.")
            return 0
        counts = apply_plan(conn, plan, now, first)
    print(f"\nЗаписано: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
