"""
Cron-скрипт для еженедельной рассылки.
Запускается отдельно от основного бота, не делает polling.
Использование:
  python weekly.py --shift day
  python weekly.py --shift night
  python weekly.py --shift all
  python weekly.py --expire          # Удалить водителей, не ответивших за 2 часа
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import telegram
from telegram import ReplyKeyboardMarkup

from config import Config, Buttons
from i18n import t, button
from models import ShiftType
from persistence import get_state_manager
from sheets import SheetManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def kb_yes_no(tg_id: int | None = None):
    return ReplyKeyboardMarkup(
        [[button("btn.yes", tg_id), button("btn.no", tg_id)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def send_weekly(bot, sheets, state, config, tg_id: int, shift: str):
    dp = sheets.get_driver_passengers(tg_id)
    passengers = dp.passengers if dp else []

    passengers_block = "\n".join(passengers) if passengers else t("weekly.no_passengers", tg_id=tg_id)
    txt = t("weekly.greeting", tg_id=tg_id, passengers=passengers_block)

    try:
        await bot.send_message(
            chat_id=tg_id,
            text=txt,
            reply_markup=kb_yes_no(tg_id),
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
    elif shift_arg == "meltech_day":
        target_shifts = {ShiftType.MELTECH_DAY}
    elif shift_arg == "meltech_night":
        target_shifts = {ShiftType.MELTECH_NIGHT}
    else:  # all
        target_shifts = {ShiftType.DAY, ShiftType.NIGHT, ShiftType.MELTECH_DAY, ShiftType.MELTECH_NIGHT}

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


# Молчание: через 2 часа — одно напоминание, через сутки — очистка списка.
# Раньше список стирался уже через 2 часа: ночную смену спрашивали днём,
# водитель спал, его пассажиры освобождались и уходили к другим водителям.
REMIND_AFTER_SECONDS = 2 * 60 * 60
EXPIRE_TIMEOUT_SECONDS = 24 * 60 * 60


async def remind_unanswered(bot, sheets, state, config):
    """Одно напоминание тем, кто молчит дольше двух часов."""
    for tg_id, shift in state.get_unreminded(REMIND_AFTER_SECONDS):
        try:
            dp = sheets.get_driver_passengers(tg_id)
            passengers = dp.passengers if dp else []
            pax = "\n".join(passengers) if passengers else t("weekly.no_passengers", tg_id=tg_id)
            await bot.send_message(
                chat_id=tg_id,
                text=t("weekly.reminder", tg_id=tg_id, passengers=pax),
                reply_markup=kb_yes_no(tg_id),
            )
        except Exception as e:
            logger.warning(f"remind: failed for tg_id={tg_id}: {e}")
        # Отмечаем даже при сбое отправки: повторять каждые 15 минут нельзя.
        state.mark_reminded(tg_id)
        if config.ADMIN_CHAT_ID:
            try:
                await bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=f"⏰ Напоминание о проверке: tg_id={tg_id} shift={shift}",
                )
            except Exception:
                pass
        await asyncio.sleep(0.1)


async def expire_unanswered(bot, sheets, state, config):
    """Не ответил на weekly check за сутки → чистим ТОЛЬКО его пассажиров.

    Через 2 часа молчания сначала приходит напоминание (remind_unanswered).
    Запись водителя (машина, город, номера) НЕ удаляется — обнуляется лишь его
    список пассажиров, а Rides with пассажиров сбрасывается.
    """
    expired = state.get_expired(EXPIRE_TIMEOUT_SECONDS)
    if not expired:
        logger.info("expire: no expired pending confirmations")
        await remind_unanswered(bot, sheets, state, config)
        return

    logger.info(f"expire: found {len(expired)} unanswered drivers")

    removed = 0
    failed = 0

    for tg_id, shift in expired:
        try:
            dp = sheets.get_driver_passengers(tg_id)
            old_passengers = list(dp.passengers) if dp else []

            if dp and old_passengers:
                # Пишем ДО очистки: 4 октября так стёрло 63 списка и 115
                # пассажиров, и восстанавливать пришлось раскопками по
                # посуточным снимкам. Журнал делает это одной командой.
                from db import store

                store.log_event(
                    actor=f"bot:{tg_id}", action="weekly_clear",
                    subject=dp.driver_name or str(tg_id),
                    details={"telegram_id": tg_id, "shift": shift,
                             "passengers": old_passengers},
                )
                dp.passengers = []
                try:
                    sheets.upsert_driver_passengers(dp)
                    sheets.clear_rides_with(names=set(old_passengers))
                except Exception as e:
                    # Откат: восстанавливаем список пассажиров
                    try:
                        dp.passengers = old_passengers
                        sheets.upsert_driver_passengers(dp)
                    except Exception:
                        pass
                    raise e

            state.remove_pending(tg_id)
            removed += 1

            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=t("weekly.expired_cleared", tg_id=tg_id),
                )
            except Exception:
                pass

            if config.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=config.ADMIN_CHAT_ID,
                        text=(
                            f"⏰ Expire (сутки без ответа): очищены пассажиры водителя tg_id={tg_id} "
                            f"shift={shift} было={len(old_passengers)} (водитель сохранён)"
                        ),
                    )
                except Exception:
                    pass
        except Exception as e:
            failed += 1
            logger.error(f"expire: failed for tg_id={tg_id}: {e}")
            if config.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=config.ADMIN_CHAT_ID,
                        text=f"⚠️ Expire failed for tg_id={tg_id}: {str(e)[:500]}",
                    )
                except Exception:
                    pass

        await asyncio.sleep(0.1)

    logger.info(f"expire: removed={removed} failed={failed}")
    await remind_unanswered(bot, sheets, state, config)


async def run_expire():
    config = Config()
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)
    sheets = SheetManager(config)
    state = get_state_manager(config.STATE_FILE)
    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
    await expire_unanswered(bot, sheets, state, config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--shift",
        choices=["day", "night", "meltech_day", "meltech_night", "all"],
        default="all",
        help="Какую смену рассылать (day/night/all)",
    )
    parser.add_argument(
        "--expire",
        action="store_true",
        help="Очистить списки водителей, не ответивших на weekly check за сутки",
    )
    args = parser.parse_args()
    if args.expire:
        asyncio.run(run_expire())
    else:
        asyncio.run(run(args.shift))


if __name__ == "__main__":
    main()