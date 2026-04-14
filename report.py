"""
Standalone scripts for report notifications via Telegram.
Run separately from the main bot (like weekly.py).

Usage:
  python report.py --mode daily     # Send daily snapshot summary
  python report.py --mode biweekly  # Send bi-weekly report + anomalies
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

import telegram

from config import Config
from sheets import SheetManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def daily_summary(bot, sheets, config):
    """Read current drivers_passengers state and send brief stats to admin."""
    values = sheets._values(config.DRIVERS_PASSENGERS_SHEET)
    if not values or len(values) < 2:
        total = 0
        with_2plus = 0
        without = 0
    else:
        headers = values[0]
        col = sheets._col_map(headers)
        p_cols = [col.get(f"Passenger{i}") for i in range(1, 5)]
        p_cols = [c for c in p_cols if c is not None]
        name_col = col.get("Name")

        total = 0
        with_2plus = 0
        without = 0

        for row in values[1:]:
            if name_col is None or name_col >= len(row):
                continue
            name = (row[name_col] or "").strip()
            if not name:
                continue
            total += 1
            pax_count = sum(
                1 for c in p_cols
                if c < len(row) and (row[c] or "").strip()
            )
            if pax_count >= 2:
                with_2plus += 1
            elif pax_count == 0:
                without += 1

    today = datetime.now().strftime("%d.%m.%Y")
    text = (
        f"\U0001f4f8 Snapshot {today}\n"
        f"Водителей: {total} | "
        f"С 2+ пассажирами: {with_2plus} | "
        f"Без пассажиров: {without}"
    )

    if config.ADMIN_CHAT_ID:
        try:
            await bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=text)
            logger.info("Daily summary sent to admin chat")
        except Exception as e:
            logger.error("Failed to send daily summary: %s", e)


async def biweekly_report(bot, sheets, config):
    """Read Svodka + _anomalies sheets and send formatted report to admin."""
    try:
        svodka_values = sheets._values("Svodka")
    except Exception:
        svodka_values = None

    if not svodka_values or len(svodka_values) < 2:
        if config.ADMIN_CHAT_ID:
            await bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text="\u26a0\ufe0f Отчёт не найден. Сначала запусти generateBiWeeklyReport() в GAS.",
            )
        return

    header = svodka_values[0]
    label_a = header[1] if len(header) > 1 else "Week A"
    label_b = header[2] if len(header) > 2 else "Week B"

    lines = [f"\U0001f4ca Сводка за 2 недели\n{label_a} | {label_b}\n"]
    for row in svodka_values[1:]:
        name = row[0] if len(row) > 0 else ""
        days_a = row[1] if len(row) > 1 else 0
        days_b = row[2] if len(row) > 2 else 0
        comment = row[3] if len(row) > 3 else "-"
        if not name:
            continue
        flag = "" if comment == "-" else " \u26a0\ufe0f"
        lines.append(f"  {name}: {days_a} | {days_b}{flag}")

    summary_text = "\n".join(lines)

    # Read anomalies
    try:
        anom_values = sheets._values("_anomalies")
    except Exception:
        anom_values = None

    anomaly_text = ""
    if anom_values and len(anom_values) > 1:
        by_type = {}
        for row in anom_values[1:]:
            atype = row[1] if len(row) > 1 else "UNKNOWN"
            driver = row[2] if len(row) > 2 else ""
            details = row[3] if len(row) > 3 else ""
            week = row[4] if len(row) > 4 else ""
            if atype not in by_type:
                by_type[atype] = []
            by_type[atype].append(f"  {driver}: {details} ({week})")

        anom_lines = ["\n\n\u26a0\ufe0f Аномалии:"]
        for atype, entries in by_type.items():
            anom_lines.append(f"\n{atype} ({len(entries)}):")
            for entry in entries[:10]:
                anom_lines.append(entry)
            if len(entries) > 10:
                anom_lines.append(f"  ... и ещё {len(entries) - 10}")

        anomaly_text = "\n".join(anom_lines)

    full_text = summary_text + anomaly_text

    if config.ADMIN_CHAT_ID:
        # Telegram message limit is 4096 chars
        for i in range(0, len(full_text), 4000):
            chunk = full_text[i:i + 4000]
            try:
                await bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=chunk)
            except Exception as e:
                logger.error("Failed to send report chunk: %s", e)
            await asyncio.sleep(0.1)

    logger.info("Bi-weekly report sent to admin chat")


async def run(mode: str):
    config = Config()
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    sheets = SheetManager(config)
    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)

    if mode == "daily":
        await daily_summary(bot, sheets, config)
    elif mode == "biweekly":
        await biweekly_report(bot, sheets, config)
    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["daily", "biweekly"],
        required=True,
        help="daily = snapshot summary, biweekly = full report + anomalies",
    )
    args = parser.parse_args()
    asyncio.run(run(args.mode))


if __name__ == "__main__":
    main()
