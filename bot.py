from __future__ import annotations

import logging

from admin_log import format_exception

from telegram import Update
from telegram.ext import ContextTypes
from telegram.request import HTTPXRequest

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

from config import Config
from i18n import button_regex
from sheets import SheetManager
from handlers import (
    BotHandlers,
    ST_DRIVER_NAME,
    ST_DRIVER_CAR,
    ST_DRIVER_PLATES,
    ST_ADD_PASSENGERS,
    ST_STOP_CONFIRM,
    ST_ADMIN_MODE,
    ST_ADMIN_TGID,
    ST_ADMIN_SHIFT,
    ST_REMOVE_PASSENGER,
    ST_BROADCAST_CONFIRM,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_app():
    config = Config()

    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    sheets = SheetManager(config)
    handlers = BotHandlers(config, sheets)

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )

    app = Application.builder().token(
        config.TELEGRAM_BOT_TOKEN
    ).request(request).build()

    async def on_error(update, context):
        if not config.ADMIN_CHAT_ID:
            return
        try:
            u = getattr(update, "effective_user", None)
            meta = ""
            if u:
                meta = f"\n(uid={u.id} @{u.username})" if u.username else f"\n(uid={u.id})"
            await context.bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=(
                    "🧾 Exception" + meta + "\n" + format_exception(context.error)
                )[-3500:],
            )
        except Exception:
            pass

    app.add_error_handler(on_error)

    async def log_incoming_railway(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        c = update.effective_chat
        if not u:
            return

        payload = ""
        if update.message and update.message.text:
            payload = update.message.text
        elif update.callback_query and update.callback_query.data:
            payload = f"[callback] {update.callback_query.data}"
        else:
            payload = "[non-text update]"

        logger.info(
            "INCOMING uid=%s username=@%s name=%s chat_id=%s chat_type=%s payload=%r",
            u.id,
            u.username or "",
            u.full_name,
            c.id if c else None,
            c.type if c else None,
            payload,
        )

    app.add_handler(MessageHandler(filters.ALL, log_incoming_railway), group=-1)

    # Regex patterns that match buttons in any supported language
    re_become_driver     = f"^({button_regex('btn.become_driver')})$"
    re_add_passengers    = f"^({button_regex('btn.add_passengers')})$"
    re_stop_being_driver = f"^({button_regex('btn.stop_being_driver')})$"
    re_remove_passenger  = f"^({button_regex('btn.remove_passenger')})$"
    re_admin_weekly      = f"^({button_regex('btn.admin_weekly_target')})$"
    re_cancel            = f"^({button_regex('btn.cancel')})$"
    re_my_record         = f"^({button_regex('btn.my_record')})$"
    re_yes_no            = f"^({button_regex('btn.yes', 'btn.no')})$"

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", handlers.start),
            MessageHandler(filters.Regex(re_become_driver), handlers.become_driver_start),
            MessageHandler(filters.Regex(re_add_passengers), handlers.add_passengers_start),
            MessageHandler(filters.Regex(re_stop_being_driver), handlers.stop_being_driver_start),
            MessageHandler(filters.Regex(re_remove_passenger), handlers.remove_passenger_start),
            MessageHandler(filters.Regex(re_admin_weekly), handlers.admin_weekly_start),
            CommandHandler("broadcast", handlers.broadcast),
            MessageHandler(filters.Regex(re_cancel), handlers.cancel),
        ],
        states={
            ST_DRIVER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.become_driver_name)
            ],
            ST_DRIVER_CAR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.become_driver_car)
            ],
            ST_DRIVER_PLATES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.become_driver_plates)
            ],
            ST_ADD_PASSENGERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.add_passengers_input)
            ],
            ST_STOP_CONFIRM: [
                MessageHandler(filters.Regex(re_yes_no), handlers.stop_being_driver_confirm)
            ],
            ST_REMOVE_PASSENGER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.remove_passenger_input)
            ],
            ST_ADMIN_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.admin_mode)
            ],
            ST_ADMIN_TGID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.admin_tgid)
            ],
            ST_ADMIN_SHIFT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.admin_shift)
            ],
            ST_BROADCAST_CONFIRM: [
                MessageHandler(filters.Regex(re_yes_no), handlers.broadcast_confirm)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(re_cancel), handlers.cancel),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    app.add_handler(MessageHandler(filters.Regex(re_my_record), handlers.my_record))

    app.add_handler(CommandHandler("broadcast_keyboard", handlers.broadcast_keyboard))
    app.add_handler(CommandHandler("report", handlers.report_command))
    app.add_handler(CommandHandler("english", handlers.set_language_english))
    app.add_handler(CommandHandler("russian", handlers.set_language_russian))

    app.add_handler(MessageHandler(filters.Regex(re_yes_no), handlers.weekly_answer))

    app.add_handler(MessageHandler(filters.ALL, handlers.unknown))

    app.job_queue.run_repeating(
        handlers.expire_job,
        interval=15 * 60,
        first=60,
        name="expire_unanswered_weekly_checks",
    )

    return app


if __name__ == "__main__":
    application = build_app()
    application.run_polling(drop_pending_updates=True, allowed_updates=None)
