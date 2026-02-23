import logging
import os
import time
import threading
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import asyncio

from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import load_config, Buttons
from sheets import SheetManager
from handlers import BotHandlers, ADD_NAME, ADD_CAR, ADD_PLATES, PASS_INPUT, DEL_INPUT
from persistence import init_state_manager, get_state_manager

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
# WEEKLY HTTP TRIGGER SERVER
# =========================

def _parse_utc_iso(ts: str) -> datetime | None:
    """Parse timestamp saved as UTC isoformat (naive) into aware UTC datetime."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        # state stores datetime.utcnow().isoformat() => naive UTC
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    except Exception:
        return None


def _already_triggered_today(shift: str, tz_name: str) -> bool:
    """Prevent double-run: if last_weekly_check for shift is already today in local tz."""
    state = get_state_manager()
    last = state.get_last_weekly_check(shift)
    last_dt_utc = _parse_utc_iso(last)
    if not last_dt_utc:
        return False

    tz = ZoneInfo(tz_name)
    last_local_date = last_dt_utc.astimezone(tz).date()
    now_local_date = datetime.now(tz).date()
    return last_local_date == now_local_date


def start_weekly_trigger_server(
    *,
    app: Application,
    handlers: BotHandlers,
    host: str,
    port: int,
    token: str,
):
    """
    Start a tiny HTTP server in a background thread.

    Endpoints:
      GET /health
      GET /weekly?shift=day|night&token=...
      GET /weekly/day?token=...
      GET /weekly/night?token=...
    """
    loop = asyncio.get_running_loop()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str):
            body_bytes = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def log_message(self, format, *args):
            # reduce noise
            logger.info("HTTP %s - %s", self.address_string(), format % args)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "" or path == "/":
                return self._send(200, "OK")

            if path == "/health":
                return self._send(200, "healthy")

            # token check
            req_token = (qs.get("token") or [""])[0]
            if not token or req_token != token:
                return self._send(401, "unauthorized")

            # shift from path or query
            shift = (qs.get("shift") or [""])[0].lower()
            if path == "/weekly/day":
                shift = "day"
            elif path == "/weekly/night":
                shift = "night"

            if shift not in ("day", "night"):
                return self._send(400, "bad request: shift must be day|night")

            force = (qs.get("force") or ["0"])[0] == "1"
            if not force and _already_triggered_today(shift, handlers.config.TIMEZONE):
                return self._send(200, f"skipped: already triggered today ({shift})")

            async def _run_weekly():
                fake_job = type("Job", (), {"data": shift, "name": "manual"})()
                fake_context = type("Context", (), {
                    "job": fake_job,
                    "bot": app.bot,
                    "job_queue": app.job_queue,
                })()
                logger.info("HTTP trigger: starting weekly_check shift=%s", shift)
                await handlers.weekly_check(fake_context)
                logger.info("HTTP trigger: finished weekly_check shift=%s", shift)

            fut = asyncio.run_coroutine_threadsafe(_run_weekly(), loop)
            try:
                # wait a bit to catch immediate errors; not waiting full execution
                fut.result(timeout=5)
            except asyncio.TimeoutError:
                # weekly continues in background
                pass
            except Exception as e:
                logger.exception("HTTP trigger failed: %s", e)
                return self._send(500, f"error: {e}")

            return self._send(200, f"triggered weekly: {shift}")

    server = ThreadingHTTPServer((host, port), Handler)

    def _serve():
        logger.info("Weekly trigger HTTP server listening on %s:%s", host, port)
        try:
            server.serve_forever()
        except Exception as e:
            logger.exception("HTTP server stopped: %s", e)

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    # store for shutdown (optional)
    app.bot_data["weekly_http_server"] = server


# =========================
# MAIN
# =========================

def main():
    setup_logging()
    logging.info("Starting Telegram Driver Bot...")

    config = load_config()
    logging.info(f"Configuration loaded. Timezone: {config.TIMEZONE}")

    # Initialize state manager
    init_state_manager(config.STATE_FILE)
    logging.info(f"State manager initialized with file: {config.STATE_FILE}")

    sheets = SheetManager(config)
    handlers = BotHandlers(config, sheets)

    # --- HTTP trigger settings ---
    trigger_token = os.environ.get("WEEKLY_TRIGGER_TOKEN", "").strip()
    if not trigger_token:
        logging.warning("WEEKLY_TRIGGER_TOKEN is not set. Weekly HTTP trigger will reject all requests.")

    http_host = "0.0.0.0"
    http_port = int(os.environ.get("PORT", "8080"))

    async def post_init(application: Application) -> None:
        """Delete webhook to avoid conflicts, then start HTTP trigger server."""
        await application.bot.delete_webhook(drop_pending_updates=True)

        # start the HTTP server inside event loop context
        start_weekly_trigger_server(
            app=application,
            handlers=handlers,
            host=http_host,
            port=http_port,
            token=trigger_token,
        )

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

    # Error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error(f"Exception while handling an update: {context.error}")

        if update and hasattr(update, 'effective_message') and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Произошла ошибка. Попробуйте позже или свяжитесь с администратором."
                )
            except Exception:
                pass

    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", handlers.start_cmd))
    app.add_handler(CommandHandler("shutdown", handlers.shutdown_cmd))
    app.add_handler(CommandHandler("my_driver", handlers.my_driver_cmd))
    app.add_handler(CommandHandler("cancel", handlers.cancel_cmd))

    # Conversations
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

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.MY}$"), handlers.my_driver_cmd), group=1)
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.SHUTDOWN}$"), handlers.shutdown_cmd), group=1)
    app.add_handler(MessageHandler(filters.Regex(f"^{Buttons.FORCE_WEEKLY}$"), handlers.force_weekly_check), group=1)

    # Weekly yes/no answer handler
    app.add_handler(
        MessageHandler(filters.Regex(r"^(Да|да|Нет|нет)$"), handlers.weekly_answer_handler),
        group=2
    )

    # IMPORTANT: we DO NOT schedule run_daily here anymore.
    # Railway Cron will call /weekly endpoint, and the bot's job_queue will handle timeouts.

    logging.info("Bot is ready. Starting polling...")
    print("=" * 50)
    print("🚗 TELEGRAM DRIVER BOT STARTED")
    print(f"Timezone: {config.TIMEZONE}")
    print(f"HTTP trigger: http://<host>/weekly?shift=day|night&token=*** on port {http_port}")
    print(f"State file: {config.STATE_FILE}")
    print("=" * 50)

    logging.info("Waiting 30 seconds for old instance to shut down...")
    time.sleep(30)

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