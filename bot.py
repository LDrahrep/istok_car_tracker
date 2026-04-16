from __future__ import annotations

import logging
import re

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
        read_timeout=120.0,     # важно для long-polling
        write_timeout=20.0,
        pool_timeout=20.0,
    )

    app = Application.builder().token(
        config.TELEGRAM_BOT_TOKEN
    ).request(request).build()

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

    async def log_incoming_railway(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Log every incoming update to Railway logs (console).

        We intentionally do NOT spam the admin chat with every message.
        Important events are already sent via handlers.log_admin(...) and on_error.
        """
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

    # ВАЖНО: group=-1 чтобы логировать до ConversationHandler
    app.add_handler(MessageHandler(filters.ALL, log_incoming_railway), group=-1)


    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", handlers.start),
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.BECOME_DRIVER)}$"),
                handlers.become_driver_start,
            ),
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.ADD_PASSENGERS)}$"),
                handlers.add_passengers_start,
            ),
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.STOP_BEING_DRIVER)}$"),
                handlers.stop_being_driver_start,
            ),
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.REMOVE_PASSENGER)}$"),
                handlers.remove_passenger_start,
            ),
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.ADMIN_WEEKLY_TARGET)}$"),
                handlers.admin_weekly_start,
            ),
            CommandHandler("broadcast", handlers.broadcast),
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.CANCEL)}$"),
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
            ST_BROADCAST_CONFIRM: [
                MessageHandler(
                    filters.Regex(f"^({Buttons.YES}|{Buttons.NO})$"),
                    handlers.broadcast_confirm,
                )
            ],
        },
        fallbacks=[
            MessageHandler(
                filters.Regex(f"^{re.escape(Buttons.CANCEL)}$"),
                handlers.cancel,
            )
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # My record — вне conversation, просто показывает данные
    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(Buttons.MY_RECORD)}$"),
            handlers.my_record,
        )
    )

    # Админская команда: разослать обновлённую клавиатуру всем
    app.add_handler(CommandHandler("broadcast_keyboard", handlers.broadcast_keyboard))

    app.add_handler(CommandHandler("report", handlers.report_command))

    # Weekly YES/NO ответы
    app.add_handler(
        MessageHandler(
            filters.Regex(f"^({Buttons.YES}|{Buttons.NO})$"),
            handlers.weekly_answer,
        )
    )

    # Ловим всё, что не обработалось ни одним хендлером выше.
    # ВАЖНО: группа 0 (по умолчанию), НЕ отдельная группа!
    # В одной группе python-telegram-bot останавливается на первом совпадении,
    # поэтому если ConversationHandler обработал — unknown НЕ сработает.
    app.add_handler(MessageHandler(filters.ALL, handlers.unknown))

    # JobQueue: каждые 15 минут удаляем водителей, не ответивших на weekly check за 2 часа.
    # Работает в том же процессе что и handlers, поэтому bot_state.json синхронизирован.
    app.job_queue.run_repeating(
        handlers.expire_job,
        interval=15 * 60,  # 15 минут
        first=60,          # первый запуск через 60 секунд после старта
        name="expire_unanswered_weekly_checks",
    )

    return app


if __name__ == "__main__":
    application = build_app()
    application.run_polling(drop_pending_updates=True, allowed_updates=None)