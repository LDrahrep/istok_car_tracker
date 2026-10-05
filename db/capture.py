"""Параллельный захват снапшотов карпулов из Google Sheets в Postgres.

Работает РЯДОМ с GAS и ничего в таблице не меняет — только читает. Поэтому
запуск безопасен в любой момент: бот, триггеры и сводки продолжают работать
как работали.

Два режима:
    --live              снимок текущего drivers_passengers на сегодняшнюю дату
    --backfill week2    перенос уже накопленных снапшотов из week-листа

Backfill важен по времени: week1..week4 — это очередь с потерей хвоста,
каждое воскресенье week4 затирается. Всё, что там лежит сейчас, через три
ротации исчезнет навсегда — как уже исчезли снапшоты за 21–23 сентября.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Часовой пояс бизнеса. От него зависит, какой датой подписан снимок, поэтому
# он должен совпадать с тем, что использует GAS (таймзона таблицы) и tabeli.
CAPTURE_TZ = ZoneInfo(os.getenv("CAPTURE_TZ", "America/Chicago"))

MAX_PASSENGERS = 4
SNAPSHOT_KEY_RE = re.compile(r"^SK\|(\d{4}-\d{2}-\d{2})\|")


@dataclass
class SnapshotRow:
    snapshot_date: date
    telegram_id: int
    driver_name: str
    phone: str = ""
    shift_raw: str = ""
    site_raw: str = ""
    passengers: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    rows: list[SnapshotRow] = field(default_factory=list)
    read: int = 0
    skipped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


# ───────────────────────── чистые функции (тестируемые) ─────────────────────


def col_map(header: Sequence[object]) -> dict[str, int]:
    """Заголовок → индекс. Регистр и пробелы не значимы.

    В листах встречаются `telegramID`, `telegramid`, `Phone Number`/`Phone`,
    поэтому сопоставляем по нормализованному имени, а не по позиции: позиция
    ломается от любой вставленной колонки.
    """
    return {str(h).strip().lower(): i for i, h in enumerate(header) if str(h).strip()}


def pick(cols: dict[str, int], row: Sequence[object], *names: str) -> str:
    """Первая существующая колонка из списка имён, как строка без пробелов."""
    for name in names:
        idx = cols.get(name)
        if idx is not None and idx < len(row):
            value = row[idx]
            if value is not None:
                return str(value).strip()
    return ""


def parse_tgid(raw: str) -> Optional[int]:
    """Telegram ID из ячейки.

    Sheets умеет отдать число как `1.23457E+9` — это уже потеря точности,
    восстановить настоящий ID из такого представления нельзя, поэтому строка
    отбрасывается и попадает в счётчик пропусков, а не чинится наугад.
    """
    value = raw.strip()
    if value.endswith(".0"):
        value = value[:-2]
    return int(value) if value.isdigit() else None


def parse_sheet(
    values: Sequence[Sequence[object]],
    *,
    fixed_date: Optional[date] = None,
) -> ParseResult:
    """Разбирает значения листа в строки снапшота.

    `fixed_date` задаётся для live-режима (у drivers_passengers своей даты нет).
    Для week-листов дата берётся из SnapshotKey вида `SK|2026-10-05|21:00`;
    час в ключе — константа из конфига GAS, а не время прогона, поэтому
    игнорируется.
    """
    result = ParseResult()
    if not values:
        return result

    cols = col_map(values[0])
    if "name" not in cols or "telegramid" not in cols:
        result.skip("нет колонок Name/telegramID")
        return result

    passenger_names = [f"passenger{i}" for i in range(1, MAX_PASSENGERS + 1)]

    for row in values[1:]:
        if not any(str(c).strip() for c in row if c is not None):
            continue
        result.read += 1

        if fixed_date is not None:
            snapshot_date = fixed_date
        else:
            match = SNAPSHOT_KEY_RE.match(pick(cols, row, "snapshotkey"))
            if not match:
                result.skip("нет SnapshotKey")
                continue
            snapshot_date = date.fromisoformat(match.group(1))

        name = pick(cols, row, "name")
        if not name:
            result.skip("пустое имя")
            continue

        tgid = parse_tgid(pick(cols, row, "telegramid", "telegram id"))
        if tgid is None:
            result.skip("нечитаемый telegramID")
            continue

        passengers = [p for p in (pick(cols, row, n) for n in passenger_names) if p]

        result.rows.append(
            SnapshotRow(
                snapshot_date=snapshot_date,
                telegram_id=tgid,
                driver_name=name,
                phone=pick(cols, row, "phone number", "phone"),
                shift_raw=pick(cols, row, "shift"),
                site_raw=pick(cols, row, "site"),
                passengers=passengers,
            )
        )

    return result


def dedupe_last_wins(rows: Iterable[SnapshotRow]) -> list[SnapshotRow]:
    """Схлопывает дубли (водитель, дата), оставляя последнюю строку.

    Повторяет семантику GAS `getSnapshotsForWeek_`, где более поздняя строка
    перезаписывала раннюю. Нужно именно при backfill: в week2 лежит 366 дублей
    из 854 пар — два прогона append за один день.
    """
    collapsed: dict[tuple[int, date], SnapshotRow] = {}
    for row in rows:
        collapsed[(row.telegram_id, row.snapshot_date)] = row
    return list(collapsed.values())


def today_in_business_tz() -> date:
    return datetime.now(CAPTURE_TZ).date()


# ──────────────────────────────── ввод-вывод ────────────────────────────────


def read_sheet(sheet_name: str) -> list[list[str]]:
    """Значения листа целиком через SheetManager — ради его retry и кэша."""
    from config import Config
    from sheets import SheetManager

    return SheetManager(Config())._values(sheet_name)


def run(source: str, sheet_name: str, fixed_date: Optional[date]) -> int:
    """CLI-обёртка. Запись и журналирование живут в store, чтобы SQL
    существовал в одном экземпляре, а не в двух расходящихся копиях."""
    from config import Config
    from sheets import SheetManager

    from . import store

    if not store.enabled():
        print("DATABASE_URL не задан", file=sys.stderr)
        return 2

    store.apply_schema()
    sheets = SheetManager(Config())
    info = store.capture(sheets, sheet_name, source=source, fixed_date=fixed_date)

    print(f"[{source}] лист={sheet_name} прочитано={info['read']} "
          f"вставлено={info['inserted']} обновлено={info['updated']} "
          f"пропущено={info['skipped']} схлопнуто_дублей={info['collapsed']}")
    for reason, count in sorted(info["reasons"].items()):
        print(f"    пропуск «{reason}»: {count}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true",
                       help="снять текущий drivers_passengers на сегодня")
    group.add_argument("--backfill", metavar="SHEET",
                       help="перенести снапшоты из week-листа, напр. week2")
    parser.add_argument("--date", help="переопределить дату для --live (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    if args.live:
        from config import Config

        snapshot_date = date.fromisoformat(args.date) if args.date else today_in_business_tz()
        return run("live", Config().DRIVERS_PASSENGERS_SHEET, snapshot_date)

    return run(f"backfill:{args.backfill}", args.backfill, None)


if __name__ == "__main__":
    raise SystemExit(main())
