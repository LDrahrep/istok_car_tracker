"""Запись ростера в Postgres: site, person, presence.

Разделение с importer.py намеренное: там разбор (чистые функции, покрытые
тестами без БД), здесь — соединение, порядок вставок и разрешение конфликтов.
Проверять логику разбора можно без Postgres, а это полезно: Postgres живёт
внутри Railway и локально недоступен.

Направление одностороннее, Sheets → БД. Пока бот не читает из person/presence
ни строки, импорт не способен повлиять на работу людей — поэтому его можно
запускать смело и смотреть результат в админке.

Что из этого следует для правок в гриде: `person.shift` берётся из листа
employees, то есть ручная правка смены в панели будет перезаписана следующим
импортом. Так и задумано на первом этапе — владелец справочника сотрудников
пока Sheets. А `site.name` импорт только создаёт и никогда не обновляет, так
что переименовать объект в панели можно, и это сохранится.
"""
from __future__ import annotations

import logging
from typing import Optional

from .importer import (
    SITE_ALIASES,
    PersonRow,
    build_carpool,
    build_people,
    is_timesheet,
    suspicious_sheets,
    name_key,
    parse_timesheet,
    resolve_telegram_conflicts,
    sites_referenced,
)
from .store import _connect, enabled

logger = logging.getLogger(__name__)

SITE_INSERT = """
INSERT INTO site (id, name) VALUES (%s, %s)
ON CONFLICT (id) DO NOTHING
"""

# Освобождаем telegram_id, который переезжает на другое имя: иначе UNIQUE
# уронит всю пачку. Живой случай — водителя переименовали, старая строка в
# листе drivers осталась.
# Переименование водителя. telegram_id переживает смену имени, поэтому для
# водителей переименование отличимо от появления нового человека — и запись
# обновляется на месте, вместо второго человека с той же историей.
# NOT EXISTS: если под новым именем запись уже есть (например, созданная из
# табеля), переименовать в неё нельзя — это слияние, оно делается вручную.
RENAME_BY_TGID = """
UPDATE person p
SET full_name = v.full_name, name_key = v.name_key, updated_at = now()
FROM (SELECT unnest(%s::bigint[]) AS telegram_id,
             unnest(%s::text[])   AS full_name,
             unnest(%s::text[])   AS name_key) v
WHERE p.telegram_id = v.telegram_id
  AND p.name_key <> v.name_key
  AND NOT EXISTS (SELECT 1 FROM person q WHERE q.name_key = v.name_key)
"""

RELEASE_TGIDS = """
UPDATE person SET telegram_id = NULL, updated_at = now()
WHERE telegram_id = ANY(%s) AND name_key <> ALL(%s)
"""

PERSON_UPSERT = """
INSERT INTO person (full_name, name_key, shift, telegram_id, current_site_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (name_key) DO UPDATE SET
    full_name       = EXCLUDED.full_name,
    -- 'unknown' не затирает уже известную смену: табель знает человека,
    -- но не знает его смену, и терять её из-за этого незачем.
    shift           = CASE WHEN EXCLUDED.shift <> 'unknown'
                           THEN EXCLUDED.shift ELSE person.shift END,
    telegram_id     = COALESCE(EXCLUDED.telegram_id, person.telegram_id),
    -- Текущий объект следует за последней отметкой: переезд человека виден
    -- из табеля, вести колонку руками не нужно (решение гриля №4).
    current_site_id = COALESCE(EXCLUDED.current_site_id, person.current_site_id),
    updated_at      = now()
"""

# Связи пересобираются целиком из снапшотов, поэтому производные строки
# сносятся и пишутся заново. DO NOTHING оставляет приоритет ручным строкам:
# правка администратора не должна молча затираться импортом.
CARPOOL_INSERT = """
INSERT INTO carpool (ride_date, driver_id, passenger_id, seat, source)
VALUES (%s, %s, %s, %s, 'snapshot')
ON CONFLICT (ride_date, passenger_id) DO NOTHING
"""

PRESENCE_INSERT = """
INSERT INTO presence (person_id, work_date, site_id, source)
VALUES (%s, %s, %s, %s)
ON CONFLICT (person_id, work_date, site_id) DO NOTHING
"""


def _driver_tgids(sheets, drivers_sheet: str) -> dict[str, int]:
    """name_key → собственный telegram_id, из листа drivers.

    Читаем ВСЕ строки, включая isActive=FALSE: telegram_id — это личность
    человека, а не его текущий статус водителя. Если потерять связь у
    неактивного, при повторной регистрации он приедет как новый человек.
    """
    values = sheets._values(drivers_sheet)
    if not values or len(values) < 2:
        return {}
    headers = [h.strip() for h in values[0]]
    try:
        i_name = headers.index("Name")
        i_tg = headers.index("telegramID")
    except ValueError:
        logger.warning("лист %s: нет колонок Name/telegramID", drivers_sheet)
        return {}

    out: dict[str, int] = {}
    for row in values[1:]:
        if i_name >= len(row) or i_tg >= len(row):
            continue
        raw_tg = str(row[i_tg]).strip()
        key = name_key(row[i_name])
        if key and raw_tg.isdigit():
            out.setdefault(key, int(raw_tg))
    return out


def _count(cur, table: str) -> int:
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


def import_roster(sheets, *, drivers_sheet: str,
                  only: Optional[str] = None) -> dict:
    """Читает табели и справочники, наполняет site/person/presence.

    `only` — подстрока имени листа, чтобы сузить прогон. Это не украшение:
    каждый табель — отдельный запрос к Google, а квота около 60 запросов в
    минуту почти выбирается еженедельной рассылкой. Полный прогон делать
    в спокойное время.
    """
    if not enabled():
        return {"enabled": False}

    all_titles = sheets.sheet_titles()
    skipped = suspicious_sheets(all_titles)
    titles = [t for t in all_titles if is_timesheet(t)]
    if only:
        needle = only.casefold()
        titles = [t for t in titles if needle in t.casefold()]

    presence_rows = []
    per_sheet: dict[str, int] = {}
    for title in titles:
        rows = parse_timesheet(sheets._values(title), title)
        per_sheet[title] = len(rows)
        presence_rows.extend(rows)

    employees = [(e.name, e.shift) for e in sheets.get_all_employees() if e.name]
    tgids = _driver_tgids(sheets, drivers_sheet)

    people = build_people(employees, tgids, presence_rows)
    people, tg_conflicts = resolve_telegram_conflicts(people)
    # Справочник объектов не должен зависеть от того, кто отметился на этой
    # неделе: иначе объект, где сейчас никого нет, исчезнет из выпадающего
    # списка в админке, и назначить туда человека будет нечем. Поэтому
    # каноничный набор плюс всё, на что реально ссылаются строки.
    sites = sorted(set(SITE_ALIASES.values())
                   | set(sites_referenced(people, presence_rows)))

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO capture_run (source, rows_read) VALUES (%s, %s)"
                " RETURNING id",
                (f"import:{len(titles)}sheets", len(presence_rows)),
            )
            run_id = cur.fetchone()[0]
        try:
            with conn.cursor() as cur:
                before_sites = _count(cur, "site")
                before_people = _count(cur, "person")
                before_presence = _count(cur, "presence")

                cur.executemany(SITE_INSERT, [(s, s.title()) for s in sites])

                # Переименования — до всего остального: иначе RELEASE_TGIDS
                # снимет ID со старого имени, и связь с человеком потеряется.
                with_tg = [p for p in people if p.telegram_id]
                renamed = 0
                if with_tg:
                    cur.execute(RENAME_BY_TGID, (
                        [p.telegram_id for p in with_tg],
                        [p.full_name for p in with_tg],
                        [p.name_key for p in with_tg],
                    ))
                    renamed = cur.rowcount

                claimed = [p.telegram_id for p in people if p.telegram_id]
                if claimed:
                    cur.execute(RELEASE_TGIDS, (
                        claimed, [p.name_key for p in people if p.telegram_id],
                    ))

                cur.executemany(PERSON_UPSERT, [
                    (p.full_name, p.name_key, p.shift, p.telegram_id,
                     p.current_site_id)
                    for p in people
                ])

                # person_id известен только после upsert — присутствие пишем
                # вторым проходом по готовому отображению.
                cur.execute("SELECT name_key, id FROM person")
                ids = dict(cur.fetchall())
                presence_params = []
                orphans = 0
                for r in presence_rows:
                    pid = ids.get(name_key(r.name))
                    if pid is None:
                        orphans += 1
                        continue
                    presence_params.append(
                        (pid, r.work_date, r.site_id, "timesheet")
                    )
                cur.executemany(PRESENCE_INSERT, presence_params)

                # Связи водитель↔пассажир собираются из уже лежащих в базе
                # снапшотов — лишних обращений к Google не нужно.
                cur.execute(
                    "SELECT telegram_id, id FROM person WHERE telegram_id IS NOT NULL"
                )
                by_tgid = dict(cur.fetchall())
                cur.execute(
                    "SELECT snapshot_date, telegram_id, driver_name, passengers"
                    " FROM carpool_snapshot"
                )
                links, link_problems = build_carpool(cur.fetchall(), ids, by_tgid)
                cur.execute("DELETE FROM carpool WHERE source = 'snapshot'")
                kept_manual = _count(cur, "carpool")
                cur.executemany(CARPOOL_INSERT, [
                    (l.ride_date, l.driver_id, l.passenger_id, l.seat)
                    for l in links
                ])
                after_carpool = _count(cur, "carpool")

                after_sites = _count(cur, "site")
                after_people = _count(cur, "person")
                after_presence = _count(cur, "presence")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE capture_run SET finished_at = now(), error = %s"
                    " WHERE id = %s",
                    (str(exc)[:2000], run_id),
                )
            conn.commit()
            raise

        people_new = after_people - before_people
        presence_new = after_presence - before_presence
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE capture_run SET finished_at = now(), rows_written = %s,"
                " rows_updated = %s, rows_skipped = %s WHERE id = %s",
                (presence_new, len(people) - people_new, orphans, run_id),
            )
        conn.commit()

    return {
        "enabled": True,
        "sheets": len(titles),
        "per_sheet": per_sheet,
        "presence_read": len(presence_rows),
        "sites_new": after_sites - before_sites,
        "sites_total": after_sites,
        "people_new": people_new,
        "people_total": after_people,
        "people_seen": len(people),
        "presence_new": presence_new,
        "presence_total": after_presence,
        "orphans": orphans,
        "tg_conflicts": tg_conflicts,
        "carpool_built": len(links),
        "carpool_written": after_carpool - kept_manual,
        "carpool_total": after_carpool,
        "carpool_problems": link_problems,
        "skipped_sheets": skipped,
        "renamed": renamed,
    }


def merge_people(old_name: str, new_name: str) -> dict:
    """Склеить две записи одного человека: историю к новому имени, старую удалить.

    Нужно там, где переименование неотличимо от нового человека: у пассажира
    нет telegram_id, и импорт честно создаёт вторую запись, оставляя историю
    на первой. Автоматически такие записи склеивать нельзя — два похожих
    имени могут быть двумя разными людьми (Azimbek и Azizbek реальны).
    Решение принимает человек, код только аккуратно переносит.

    Ссылка переставляется UPDATE-ом, а не копированием строки. Копия не
    работает: `carpool_pk` это (ride_date, passenger_id) БЕЗ водителя,
    поэтому строка с другим driver_id конфликтует с оригиналом по тому же
    ключу, и ON CONFLICT DO NOTHING молча её отбрасывает. На присутствии
    копирование сработало бы — там person_id входит в ключ, — и именно это
    расхождение легко принять за работающий перенос.

    NOT EXISTS отсекает строки, которые нарушили бы ограничение после
    переноса: человек уже отмечен в этот день на этом объекте, уже едет
    в этот день, или место у водителя занято. Они остаются у старой записи
    и уходят каскадом при удалении — поэтому их число возвращается отдельно,
    молча терять историю нельзя.
    """
    from .importer import name_key

    old_key, new_key = name_key(old_name), name_key(new_name)
    if not old_key or not new_key:
        return {"ok": False, "reason": "empty"}
    if old_key == new_key:
        return {"ok": False, "reason": "same"}

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name_key, id, full_name, telegram_id FROM person"
                " WHERE name_key = ANY(%s)", ([old_key, new_key],),
            )
            rows = {r[0]: r for r in cur.fetchall()}
            old_row, new_row = rows.get(old_key), rows.get(new_key)
            if old_row is None or new_row is None:
                return {"ok": False, "reason": "not_found",
                        "old_found": old_row is not None,
                        "new_found": new_row is not None}

            old_id, new_id = old_row[1], new_row[1]
            before = {}
            for label, sql in (
                ("presence", "SELECT count(*) FROM presence WHERE person_id = %s"),
                ("as_driver", "SELECT count(*) FROM carpool WHERE driver_id = %s"),
                ("as_passenger", "SELECT count(*) FROM carpool WHERE passenger_id = %s"),
            ):
                cur.execute(sql, (old_id,))
                before[label] = cur.fetchone()[0]

            moved = {}
            cur.execute(
                "UPDATE presence SET person_id = %s WHERE person_id = %s"
                " AND NOT EXISTS (SELECT 1 FROM presence x WHERE x.person_id = %s"
                "   AND x.work_date = presence.work_date AND x.site_id = presence.site_id)",
                (new_id, old_id, new_id),
            )
            moved["presence"] = cur.rowcount

            cur.execute(
                "UPDATE carpool SET driver_id = %s WHERE driver_id = %s"
                " AND passenger_id <> %s"
                " AND NOT EXISTS (SELECT 1 FROM carpool x WHERE x.ride_date = carpool.ride_date"
                "   AND x.driver_id = %s AND x.seat = carpool.seat)",
                (new_id, old_id, new_id, new_id),
            )
            moved["as_driver"] = cur.rowcount

            cur.execute(
                "UPDATE carpool SET passenger_id = %s WHERE passenger_id = %s"
                " AND driver_id <> %s"
                " AND NOT EXISTS (SELECT 1 FROM carpool x WHERE x.ride_date = carpool.ride_date"
                "   AND x.passenger_id = %s)",
                (new_id, old_id, new_id),
            )
            moved["as_passenger"] = cur.rowcount

            # telegram_id уникален: снять со старой записи нужно ДО того, как
            # ставить на новую, иначе обе держат его одновременно.
            if old_row[3] is not None:
                cur.execute("UPDATE person SET telegram_id = NULL WHERE id = %s", (old_id,))
                cur.execute(
                    "UPDATE person SET telegram_id = %s, updated_at = now()"
                    " WHERE id = %s AND telegram_id IS NULL", (old_row[3], new_id),
                )

            cur.execute("DELETE FROM person WHERE id = %s", (old_id,))
        conn.commit()

    dropped = {k: before[k] - moved[k] for k in moved}
    return {"ok": True, "old": old_row[2], "new": new_row[2],
            "moved": moved, "dropped": dropped}
