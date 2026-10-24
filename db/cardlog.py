"""Часовой журнал карточек водителей и зачёт «в любой час дня».

Снимок 21:05 — одна фотография в сутки, и всё, что было в карточке днём,
но не дожило до вечера, для доплат не существовало. Решение пользователя
24.10: пассажир засчитывается за день, если он стоял в карточке водителя
хотя бы на одном часовом снимке этого дня.

Каждый час лист drivers_passengers сравнивается с последним состоянием
в журнале, и записываются только изменившиеся карточки. Поэтому состав на
начало дня — последняя запись до него, а за день считается объединение:
состав на начало дня плюс всё, что появлялось в течение дня.

Объединение подмешивается к снимкам перед build_carpool, и дальше всё идёт
прежним путём — правило «пассажир у того, кто вписал позже», четыре места,
отчёт о неопознанных. До запуска журнала история не меняется ни на день.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

LATEST_SQL = """
SELECT DISTINCT ON (telegram_id) telegram_id, driver_name, passengers
FROM card_log ORDER BY telegram_id, captured_at DESC, id DESC
"""

INSERT_SQL = """
INSERT INTO card_log (log_date, telegram_id, driver_name, passengers)
VALUES (%s, %s, %s, %s)
"""

# Прогоны журнала видны в том же capture_run, что и снимки, — под своим
# source, чтобы догоняющий снимок (source='live') их не спутал с вечерним.
LAST_READ_SQL = """
SELECT rows_read FROM capture_run
WHERE source = 'cardlog' AND error IS NULL ORDER BY id DESC LIMIT 1
"""

RUN_SQL = """
INSERT INTO capture_run (source, rows_read, rows_written, finished_at, error)
VALUES ('cardlog', %s, %s, now(), %s)
"""


def _keys(names: Iterable[str]) -> set[str]:
    from .importer import name_key

    return {k for k in (name_key(n) for n in names or []) if k}


# ───────────────────────── чистые функции (тестируемые) ─────────────────────


def changed_cards(previous: dict[int, tuple[str, list[str]]],
                  current: dict[int, tuple[str, list[str]]]
                  ) -> list[tuple[int, str, list[str]]]:
    """Что записать в журнал: новые и изменившиеся карточки.

    Сравнение по набору нормализованных имён: перестановка колонок или
    «Shah Akmal» вместо «Akmal Shah» — не изменение. Водитель, пропавший
    из листа, записывается с пустым списком, иначе его последние пассажиры
    переносились бы на каждый следующий день.
    """
    out = []
    for tg, (name, passengers) in current.items():
        before = previous.get(tg)
        if before is None or _keys(before[1]) != _keys(passengers):
            out.append((tg, name, list(passengers)))
    for tg, (name, passengers) in previous.items():
        if tg not in current and passengers:
            out.append((tg, name, []))
    return out


def suspicious_read(previous: int, current: int) -> bool:
    """Лист прочитался подозрительно пустым — писать нельзя.

    Пустой или обрезанный ответ Google записал бы «все карточки опустели».
    Объединение за день старые поездки не потеряло бы, но со следующего дня
    карточки перестали бы переноситься. Водители не исчезают половиной разом.
    """
    if current == 0:
        return True
    return previous > 0 and current < previous / 2


def merge_day_cards(snapshots: Iterable[tuple], log: Iterable[tuple], *,
                    end: date) -> list[tuple[date, int, str, list[str]]]:
    """Снимки 21:05 плюс журнал → карточки по дням для build_carpool.

    `snapshots` — (дата, telegram_id, имя водителя, пассажиры);
    `log` — то же из card_log в порядке записи. Пассажиры снимка остаются
    на своих местах первыми, добавленные журналом идут следом. Дни без
    журнала (до его запуска) возвращаются ровно такими, как в снимке.
    """
    from .importer import name_key

    snaps = {(d, int(tg)): (name, list(p or [])) for d, tg, name, p in snapshots}

    by_tg: dict[int, list[tuple[date, str, list[str]]]] = {}
    for d, tg, name, p in log:
        by_tg.setdefault(int(tg), []).append((d, name, list(p or [])))

    from_log: dict[tuple[date, int], tuple[str, list[str]]] = {}
    for tg, entries in by_tg.items():
        entries.sort(key=lambda e: e[0])  # стабильно: внутри дня порядок записи
        state_name, state = entries[0][1], []
        i, day = 0, entries[0][0]
        while day <= end:
            union = list(state)
            while i < len(entries) and entries[i][0] == day:
                _, state_name, state = entries[i]
                union.extend(state)
                i += 1
            if union:
                from_log[(day, tg)] = (state_name, union)
            if state:
                day += timedelta(days=1)
            elif i < len(entries):
                day = entries[i][0]      # пустую карточку незачем тянуть по дням
            else:
                break

    out = []
    for key in sorted(set(snaps) | set(from_log)):
        snap = snaps.get(key)
        logged = from_log.get(key)
        if logged is None:
            out.append((key[0], key[1], snap[0], snap[1]))
            continue
        name = snap[0] if snap else logged[0]
        passengers = list(snap[1]) if snap else []
        seen = {name_key(p) for p in passengers}
        for p in logged[1]:
            k = name_key(p)
            if k and k not in seen:
                seen.add(k)
                passengers.append(p)
        out.append((key[0], key[1], name, passengers))
    return out


# ──────────────────────────────── ввод-вывод ────────────────────────────────


def day_cards(cur, *, start: Optional[date] = None,
              end: Optional[date] = None) -> list[tuple]:
    """Карточки по дням из базы: снимки с подмешанным журналом.

    Журнал читается целиком до `end`: состав на начало периода — это
    последняя запись до него, а записей там только изменения, их немного.
    """
    from .capture import today_in_business_tz

    end = end or today_in_business_tz()
    if start is None:
        cur.execute("SELECT snapshot_date, telegram_id, driver_name, passengers"
                    " FROM carpool_snapshot WHERE snapshot_date <= %s", (end,))
    else:
        cur.execute("SELECT snapshot_date, telegram_id, driver_name, passengers"
                    " FROM carpool_snapshot WHERE snapshot_date BETWEEN %s AND %s",
                    (start, end))
    snapshots = cur.fetchall()
    cur.execute("SELECT log_date, telegram_id, driver_name, passengers"
                " FROM card_log WHERE log_date <= %s ORDER BY captured_at, id", (end,))
    merged = merge_day_cards(snapshots, cur.fetchall(), end=end)
    if start is not None:
        merged = [m for m in merged if m[0] >= start]
    return merged


def log_cards(sheets, sheet_name: str, *, today: Optional[date] = None) -> dict:
    """Часовой прогон: прочитать лист и записать изменившиеся карточки."""
    from .capture import dedupe_last_wins, parse_sheet, today_in_business_tz
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}

    today = today or today_in_business_tz()
    parsed = parse_sheet(sheets._values(sheet_name), fixed_date=today)
    current = {r.telegram_id: (r.driver_name, r.passengers)
               for r in dedupe_last_wins(parsed.rows)}

    with _connect() as conn:
        with conn.cursor() as cur:
            # С чем сравнивать — с прошлым удачным прогоном, а не с журналом:
            # там копятся и пропавшие водители, и сравнение со временем врало бы.
            cur.execute(LAST_READ_SQL)
            row = cur.fetchone()
            last_read = row[0] if row else 0
            if suspicious_read(last_read, len(current)):
                logger.error("card log: лист прочитан подозрительно (%d карточек,"
                             " в прошлый раз %d) — ничего не пишу", len(current), last_read)
                cur.execute(RUN_SQL, (len(current), 0, "подозрительное чтение листа"))
                conn.commit()
                return {"enabled": True, "skipped": True, "read": len(current),
                        "last_read": last_read, "changed": 0}
            cur.execute(LATEST_SQL)
            previous = {int(tg): (name, list(p or [])) for tg, name, p in cur.fetchall()}
            changes = changed_cards(previous, current)
            cur.executemany(INSERT_SQL, [(today, tg, name, p) for tg, name, p in changes])
            cur.execute(RUN_SQL, (len(current), len(changes), None))
        conn.commit()

    return {"enabled": True, "skipped": False, "read": len(current),
            "last_read": last_read, "changed": len(changes)}
