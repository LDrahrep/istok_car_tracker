# =========================
# TELEGRAM DRIVER BOT - MAIN
# Memphis, TN (America/Chicago)
# =========================

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import load_config, Buttons
from models import ShiftType
from sheets import SheetManager
from handlers import BotHandlers, ADD_NAME, CONFIRM_PHONE, ADD_SHIFT, ADD_CAR, ADD_PLATES, PASS_INPUT, DEL_INPUT
from persistence import init_state_manager


logger = logging.getLogger(__name__)


# =========================
# LOGGING
# =========================

def setup_logging():
    """Configure logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


# =========================
# MAIN
# =========================

def main():
    """Main function to run the bot"""
    
    # Setup
    setup_logging()
    logging.info("Starting Telegram Driver Bot...")
    
    # Load configuration
    config = load_config()
    logging.info(f"Configuration loaded. Timezone: {config.TIMEZONE}")
    
    # Initialize state manager
    state_manager = init_state_manager(config.STATE_FILE)
    logging.info(f"State manager initialized with file: {config.STATE_FILE}")
    
    # Initialize sheet manager
    sheets = SheetManager(config)
    logging.info("Sheet manager initialized")
    
    # Initialize handlers
    handlers = BotHandlers(config, sheets)
    logging.info("Bot handlers initialized")
    
    # Create application
    async def post_init(application: Application) -> None:
        """Delete any active webhook to avoid conflicts with polling"""
        await application.bot.delete_webhook(drop_pending_updates=True)

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .connect_timeout(60)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .post_init(post_init)
        .build()
    )
    
    # =========================
    # REGISTER HANDLERS
    # =========================
    
    # Global error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log errors and notify user"""
        logger.error(f"Exception while handling an update: {context.error}")

        if update and hasattr(update, 'effective_message') and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Произошла ошибка. Попробуйте позже или свяжитесь с администратором."
                )
            except Exception:
                pass

    app.add_error_handler(error_handler)

    # Basic commands
    app.add_handler(CommandHandler("start", handlers.start_cmd))
    app.add_handler(CommandHandler("shutdown", handlers.shutdown_cmd))
    app.add_handler(CommandHandler("my_driver", handlers.my_driver_cmd))
    app.add_handler(CommandHandler("cancel", handlers.cancel_cmd))
    
    # Add driver conversation (group 0 = highest priority)
    add_driver_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{Buttons.ADD}$"), handlers.add_driver_start)],
        states={
            ADD_NAME: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.add_driver_name
            )],
            CONFIRM_PHONE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.confirm_phone
            )],
            ADD_SHIFT: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.add_driver_shift
            )],
            ADD_CAR: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.add_driver_car
            )],
            ADD_PLATES: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.add_driver_plates
            )],
        },
        fallbacks=[MessageHandler(filters.Regex(f"^{Buttons.CANCEL}$"), handlers.cancel_cmd)],
    )
    app.add_handler(add_driver_conv, group=0)
    
    # Passengers conversation (group 0)
    passengers_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{Buttons.PASS}$"), handlers.passengers_start)],
        states={
            PASS_INPUT: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.passengers_input
            )]
        },
        fallbacks=[MessageHandler(filters.Regex(f"^{Buttons.CANCEL}$"), handlers.cancel_cmd)],
    )
    app.add_handler(passengers_conv, group=0)
    
    # Delete passenger conversation (group 0)
    delete_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{Buttons.DEL}$"), handlers.delete_start)],
        states={
            DEL_INPUT: [MessageHandler(
                filters.TEXT & ~filters.COMMAND 
                & ~filters.Regex(f"^{Buttons.CANCEL}$")
                & ~filters.Regex(f"^{Buttons.ADD}$")
                & ~filters.Regex(f"^{Buttons.PASS}$")
                & ~filters.Regex(f"^{Buttons.DEL}$")
                & ~filters.Regex(f"^{Buttons.MY}$"),
                handlers.delete_input
            )]
        },
        fallbacks=[MessageHandler(filters.Regex(f"^{Buttons.CANCEL}$"), handlers.cancel_cmd)],
    )
    app.add_handler(delete_conv, group=0)
    
    # Menu button handlers (group 1 = lower priority)
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.MY}$"), handlers.my_driver_cmd), group=1)
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.CANCEL}$"), handlers.cancel_cmd), group=1)
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.SHUTDOWN}$"), handlers.shutdown_cmd), group=1)
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.FORCE_WEEKLY}$"), handlers.force_weekly_check), group=1)
    
    # Weekly check answer handler (group 2)
    app.add_handler(
        MessageHandler(filters.Regex(r"^(Да|да|Нет|нет)$"), handlers.weekly_answer_handler),
        group=2
    )
    
    # =========================
    # SCHEDULE WEEKLY JOBS
    # =========================
    # i luv sabina (just redeploy string)
    # Weekly check on Sundays
    app.job_queue.run_daily(
        handlers.weekly_check,
        time=config.DAY_SHIFT_TIME,
        days=(6,),  # Sunday = 6 in ISO format (0=Monday, 6=Sunday)
        data="day",
        name="weekly_day"
    )
    
    app.job_queue.run_daily(
        handlers.weekly_check,
        time=config.NIGHT_SHIFT_TIME,
        days=(6,),  # Sunday = 6 in ISO format (0=Monday, 6=Sunday)
        data="night",
        name="weekly_night"
    )
    
    logging.info(f"Scheduled weekly checks: Day at {config.DAY_SHIFT_TIME}, Night at {config.NIGHT_SHIFT_TIME}")
    
    # =========================
    # START BOT
    # =========================
    
    logging.info("Bot is ready. Starting polling...")
    print("=" * 50)
    print("🚗 TELEGRAM DRIVER BOT STARTED")
    print(f"Timezone: {config.TIMEZONE}")
    print(f"Day shift check: Sundays at {config.DAY_SHIFT_TIME}")
    print(f"Night shift check: Sundays at {config.NIGHT_SHIFT_TIME}")
    print(f"State file: {config.STATE_FILE}")
    print("=" * 50)
    
    # Run the bot
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        logging.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logging.error(f"Critical error: {e}")
        raise
    finally:
        logging.info("Bot shutdown complete")


if __name__ == "__main__":
    main()
