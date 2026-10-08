"""Сводка по доплатам из базы.

Зачем из базы, а не из GAS: список объектов в GAS зашит в код
(`istok_unified_gas.gs:1347`), и каждый новый объект — правка платёжного
движка. За один день появилось два, и самый крупный из них GAS не видит
вовсе. Здесь объект это строка справочника, и сводка подхватывает его сама.

Правило то же, что было: день засчитывается, если водитель **отмечен
в табеле** и у него **не меньше двух пассажиров**. Присутствие пассажиров
не учитывается — решение пользователя от 06.10.

Объект дня берётся из факта присутствия, а не из текущего объекта
человека: это решение гриля №4, присутствие — истина для доплат. Когда
человек отмечен в один день на двух объектах, тай-брейком служит его
`current_site_id` (решение №6).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

HEADER_NAME = "Водитель"
HEADER_DROVE = "Возил дней"
HEADER_PRESENT = "В табеле"
HEADER_COMMENT = "Комментарий"

# Составляющие зачёта по отдельности. Показывать их обязательно: число
# засчитанных дней — это пересечение двух условий, и без слагаемых его
# нельзя перепроверить глазами. Живой случай: водителю начислили 7 дней,
# хотя возил он 2, — увидеть это в одной колонке было невозможно.
TOTALS_SQL = """
WITH rides AS (
    SELECT ride_date, driver_id FROM carpool
    WHERE ride_date BETWEEN %s AND %s
    GROUP BY 1, 2 HAVING count(*) >= 2
)
SELECT p.full_name,
       (SELECT count(*) FROM rides r WHERE r.driver_id = p.id) AS drove,
       (SELECT count(DISTINCT pr.work_date) FROM presence pr
        WHERE pr.person_id = p.id AND pr.work_date BETWEEN %s AND %s) AS present
FROM person p
WHERE p.id IN (SELECT driver_id FROM rides)
"""

# Второе правило — то, по которому фактически считает GAS: день засчитывается
# за ОТМЕТКУ В ТАБЕЛЕ, а список пассажиров проверяется один раз на период, а не
# на каждый день. Оно мягче и даёт больше дней: за неделю 14–20.09 — 809 против
# 718. Разница не в коде, а в политике выплат, поэтому выбор оставлен за
# администратором, а не зашит.
BY_TIMESHEET_SQL = """
WITH drove AS (
    SELECT driver_id FROM carpool
    WHERE ride_date BETWEEN %s AND %s
    GROUP BY ride_date, driver_id HAVING count(*) >= 2
)
SELECT pr.site_id, p.full_name, date_trunc('week', pr.work_date)::date AS week_start,
       count(DISTINCT pr.work_date) AS days
FROM presence pr
JOIN person p ON p.id = pr.person_id
WHERE pr.work_date BETWEEN %s AND %s
  AND pr.person_id IN (SELECT driver_id FROM drove)
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

ROWS_SQL = """
WITH rides AS (
    SELECT ride_date, driver_id, count(*) AS pax
    FROM carpool
    WHERE ride_date BETWEEN %s AND %s
    GROUP BY 1, 2
    HAVING count(*) >= 2
), attributed AS (
    SELECT r.ride_date, r.driver_id,
           COALESCE(
               -- Тай-брейк при отметке на двух объектах: текущий объект человека.
               (SELECT pr.site_id FROM presence pr
                WHERE pr.person_id = r.driver_id AND pr.work_date = r.ride_date
                  AND pr.site_id = p.current_site_id LIMIT 1),
               (SELECT pr.site_id FROM presence pr
                WHERE pr.person_id = r.driver_id AND pr.work_date = r.ride_date
                ORDER BY pr.site_id LIMIT 1)
           ) AS site_id
    FROM rides r JOIN person p ON p.id = r.driver_id
)
SELECT a.site_id, p.full_name, date_trunc('week', a.ride_date)::date AS week_start,
       count(*) AS days
FROM attributed a JOIN person p ON p.id = a.driver_id
WHERE a.site_id IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

# Водители, у которых есть дни с 2+ пассажирами, но ни одной отметки в табеле.
# Их нельзя просто не показать: для человека это потерянные деньги, и
# причина должна быть названа.
UNMARKED_SQL = """
WITH rides AS (
    SELECT ride_date, driver_id FROM carpool
    WHERE ride_date BETWEEN %s AND %s
    GROUP BY 1, 2 HAVING count(*) >= 2
)
SELECT p.full_name, count(*) AS days
FROM rides r JOIN person p ON p.id = r.driver_id
WHERE NOT EXISTS (SELECT 1 FROM presence pr
                  WHERE pr.person_id = r.driver_id AND pr.work_date = r.ride_date)
GROUP BY 1 ORDER BY 2 DESC, 1
"""


def week_label(week_start: date) -> str:
    """«09/14 - 09/20» — ровно как в нынешней Svodka."""
    return f"{week_start:%m/%d} - {week_start + timedelta(days=6):%m/%d}"


def weeks_in(start: date, end: date) -> list[date]:
    """Понедельники всех недель, задетых периодом."""
    first = start - timedelta(days=start.weekday())
    out, cursor = [], first
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


def pivot(rows: Iterable[tuple], weeks: list[date],
          totals: Optional[dict[str, tuple[int, int]]] = None) -> dict[str, list[list[str]]]:
    """Строки (объект, водитель, неделя, дней) → таблица на каждый объект.

    Раскладка повторяет нынешнюю Svodka: имя, по колонке на неделю,
    пустой «Комментарий» — его заполняют руками, и перезаписывать его
    выгрузка не должна.
    """
    labels = [week_label(w) for w in weeks]
    totals = totals or {}
    by_site: dict[str, dict[str, dict[str, int]]] = {}
    for site_id, name, week_start, days in rows:
        by_site.setdefault(site_id, {}).setdefault(name, {})[week_label(week_start)] = days

    out: dict[str, list[list[str]]] = {}
    for site_id, drivers in sorted(by_site.items()):
        table = [[HEADER_NAME] + labels
                 + [HEADER_DROVE, HEADER_PRESENT, HEADER_COMMENT]]
        for name in sorted(drivers):
            cells = drivers[name]
            drove, present = totals.get(name, ("", ""))
            table.append([name] + [str(cells.get(l, "")) for l in labels]
                         + [str(drove), str(present), ""])
        out[site_id] = table
    return out


def sheet_title(site_id: str) -> str:
    """Лист выгрузки. Префикс обязателен — его проверяет replace_sheet."""
    return f"_db_svodka_{site_id.lower()}"


def build(start: date, end: date, *, strict: bool = True) -> dict:
    """Считает сводку.

    `strict=True` — день засчитывается, только если в ЭТОТ день было не
    меньше двух пассажиров. Это буквально то, что бот обещает водителям
    в подсказке.

    `strict=False` — правило GAS: день засчитывается за отметку в табеле,
    а двух пассажиров достаточно иметь хоть раз за период. Мягче, и
    разницу стоит понимать: за неделю 14–20.09 это 809 дней против 718.
    """
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}

    weeks = weeks_in(start, end)
    sql = ROWS_SQL if strict else BY_TIMESHEET_SQL
    params = (start, end) if strict else (start, end, start, end)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(TOTALS_SQL, (start, end, start, end))
        totals = {name: (drove, present) for name, drove, present in cur.fetchall()}
        cur.execute(sql, params)
        tables = pivot(cur.fetchall(), weeks, totals)
        cur.execute(UNMARKED_SQL, (start, end))
        unmarked = cur.fetchall()

    return {"enabled": True, "start": start, "end": end, "strict": strict,
            "weeks": [week_label(w) for w in weeks],
            "tables": tables, "unmarked": unmarked}


def export(sheets, start: date, end: date, *, strict: bool = True) -> dict:
    """Считает и кладёт по листу на объект."""
    info = build(start, end, strict=strict)
    if not info.get("enabled"):
        return info
    written = {}
    for site_id, table in info["tables"].items():
        title = sheet_title(site_id)
        sheets.replace_sheet(title, table)
        written[title] = len(table) - 1
    info["written"] = written
    return info


# Водители с живым списком, но без отметок в табеле. Еженедельная проверка
# ловит МОЛЧАНИЕ: не ответил за два часа — список чистится. Человека,
# который ушёл, но по привычке жмёт «Да», она не поймает никогда.
# Присутствие ловит: в табеле его нет, а список он подтверждает.
#
# Окно считается от последней даты, за которую вообще есть табели, а не от
# сегодня: табели заполняют с запозданием, и «нет отметок на этой неделе»
# означало бы почти всех.
STALE_SQL = """
WITH site_last AS (
    -- Последняя дата, за которую есть табель ПО ЭТОМУ объекту. Общий максимум
    -- не годится: у Fluidstack табель до 06.10, у остальных до 04.10, и окно
    -- от общего максимума отсекало бы людей с совершенно свежими отметками.
    SELECT site_id, max(work_date) AS d FROM presence GROUP BY site_id
), latest AS (SELECT max(snapshot_date) AS d FROM carpool_snapshot),
  active AS (
    SELECT s.driver_name, s.telegram_id, s.passengers,
           (SELECT p.id FROM person p WHERE p.telegram_id = s.telegram_id) AS person_id,
           (SELECT p.current_site_id FROM person p WHERE p.telegram_id = s.telegram_id) AS site_id
    FROM carpool_snapshot s, latest
    WHERE s.snapshot_date = latest.d AND cardinality(s.passengers) > 0
)
SELECT a.driver_name, a.telegram_id, a.passengers,
       (SELECT max(pr.work_date) FROM presence pr WHERE pr.person_id = a.person_id) AS last_seen,
       a.site_id,
       -- По какую дату вообще есть табель по его объекту. Без этого вывод
       -- обманчив: водитель может быть отмечен в табеле, который ещё не
       -- загрузили, и тогда это ложная тревога, а не ушедший человек.
       sl.d AS site_last
FROM active a
LEFT JOIN site_last sl ON sl.site_id = a.site_id
WHERE NOT EXISTS (
    SELECT 1 FROM presence pr
    WHERE pr.person_id = a.person_id
      AND pr.work_date > COALESCE(sl.d, (SELECT max(d) FROM site_last)) - %s::int
)
ORDER BY cardinality(a.passengers) DESC, a.driver_name
"""


def stale_drivers(days: int = 7) -> dict:
    """Кто держит пассажиров, не появляясь в табелях.

    Пассажир такого водителя заблокирован: действующий водитель получит
    «уже записан к другому водителю» и не сможет его взять.
    """
    from .store import _connect, enabled

    if not enabled():
        return {"enabled": False}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(STALE_SQL, (days,))
        rows = cur.fetchall()
    return {"enabled": True, "days": days, "rows": rows,
            "blocked": sum(len(r[2] or []) for r in rows)}
