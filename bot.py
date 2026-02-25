from __future__ import annotations

import logging

from admin_log import format_exception

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

from config import Config, Buttons
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
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_app():
    config = Config()

    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    sheets = SheetManager(config)
    handlers = BotHandlers(config, sheets)

    app = Application.builder().token(
        config.TELEGRAM_BOT_TOKEN
    ).build()

    async def on_error(update, context):
        # Любые исключения логируем в админский чат (best-effort)
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

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", handlers.start),
            MessageHandler(
                filters.Regex(f"^{Buttons.BECOME_DRIVER}$"),
                handlers.become_driver_start,
            ),
            MessageHandler(
                filters.Regex(f"^{Buttons.ADD_PASSENGERS}$"),
                handlers.add_passengers_start,
            ),
            MessageHandler(
                filters.Regex(f"^{Buttons.STOP_BEING_DRIVER}$"),
                handlers.stop_being_driver_start,
            ),
            MessageHandler(
                filters.Regex(f"^{Buttons.REMOVE_PASSENGER}$"),
                handlers.remove_passenger_start,
            ),
            MessageHandler(
                filters.Regex(f"^{Buttons.ADMIN_WEEKLY_TARGET}$"),
                handlers.admin_weekly_start,
            ),
            MessageHandler(
                filters.Regex(f"^{Buttons.CANCEL}$"),
                handlers.cancel,
            ),
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
                MessageHandler(
                    filters.Regex(f"^({Buttons.YES}|{Buttons.NO})$"),
                    handlers.stop_being_driver_confirm,
                )
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
        },
        fallbacks=[
            MessageHandler(
                filters.Regex(f"^{Buttons.CANCEL}$"),
                handlers.cancel,
            )
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # My record — вне conversation, просто показывает данные
    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{Buttons.MY_RECORD}$"),
            handlers.my_record,
        )
    )

    # Weekly YES/NO ответы
    app.add_handler(
        MessageHandler(
            filters.Regex(f"^({Buttons.YES}|{Buttons.NO})$"),
            handlers.weekly_answer,
        )
    )

    return app


if __name__ == "__main__":
    application = build_app()
    application.run_polling()