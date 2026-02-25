"""
Cron-скрипт для еженедельной рассылки.
Запускается отдельно от основного бота, не делает polling.
Использование:
  python weekly.py --shift day
  python weekly.py --shift night
  python weekly.py --shift all
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import telegram
from telegram import ReplyKeyboardMarkup

from config import Config, Buttons
from models import ShiftType
from persistence import get_state_manager
from sheets import SheetManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def kb_yes_no():
    return ReplyKeyboardMarkup(
        [[Buttons.YES, Buttons.NO]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def send_weekly(bot, sheets, state, config, tg_id: int, shift: str):
    dp = sheets.get_driver_passengers(tg_id)
    passengers = dp.passengers if dp else []

    txt = "📅 Еженедельная проверка списка пассажиров\n\n"
    txt += "Текущие пассажиры:\n"
    txt += "\n".join(passengers) if passengers else "Нет пассажиров"
    txt += "\n\nВсё актуально?"

    try:
        await bot.send_message(
            chat_id=tg_id,
            text=txt,
            reply_markup=kb_yes_no(),
        )
        state.add_pending(tg_id, shift)
        logger.info(f"Sent weekly to tg_id={tg_id} shift={shift}")

        # best-effort лог в админский чат
        if config.ADMIN_CHAT_ID:
            try:
                await bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=(
                        "🧾 Weekly send\n"
                        f"tg_id={tg_id} shift={shift} passengers={len(passengers)}"
                    ),
                )
            except Exception:
                pass
    except telegram.error.Forbidden:
        logger.warning(f"Bot blocked by user tg_id={tg_id}, skipping")
    except Exception as e:
        logger.error(f"Failed to send to tg_id={tg_id}: {e}")


async def run(shift_arg: str):
    config = Config()

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    sheets = SheetManager(config)
    state = get_state_manager(config.STATE_FILE)
    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)

    # Определяем какие смены слать
    if shift_arg == "day":
        target_shifts = {ShiftType.DAY}
    elif shift_arg == "night":
        target_shifts = {ShiftType.NIGHT}
    else:  # all
        target_shifts = {ShiftType.DAY, ShiftType.NIGHT}

    # Берём всех водителей из drivers_passengers
    values = sheets._values(config.DRIVERS_PASSENGERS_SHEET)
    if not values or len(values) < 2:
        logger.info("No drivers found, nothing to send")
        return

    headers = values[0]
    col = sheets._col_map(headers)
    tg_col = col.get("telegramID")

    if tg_col is None:
        logger.error("telegramID column not found in drivers_passengers sheet")
        sys.exit(1)

    sent = 0

    if config.ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=f"🧾 Weekly рассылка старт\nСмена: {shift_arg}",
            )
        except Exception:
            pass

    for row in values[1:]:
        if tg_col >= len(row):
            continue
        raw = row[tg_col].strip()
        if not raw.isdigit():
            continue

        tg_id = int(raw)
        driver_shift = sheets.get_shift_for_tgid(tg_id)

        if driver_shift not in target_shifts:
            continue

        await send_weekly(bot, sheets, state, config, tg_id, driver_shift.value)
        sent += 1

        # Небольшая пауза чтобы не спамить Telegram API
        await asyncio.sleep(0.1)

    logger.info(f"Weekly done: sent to {sent} drivers (shift={shift_arg})")

    # Логируем в admin чат если настроен
    if config.ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=(
                    "🧾 Weekly рассылка завершена\n"
                    f"Смена: {shift_arg}\n"
                    f"Отправлено: {sent} водителям"
                ),
            )
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--shift",
        choices=["day", "night", "all"],
        default="all",
        help="Какую смену рассылать (day/night/all)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.shift))


if __name__ == "__main__":
    main()