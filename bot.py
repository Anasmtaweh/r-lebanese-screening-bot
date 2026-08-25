import asyncio
import logging
import os
import threading

from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
from config import BOT_TOKEN
from handlers import (
    check_expired_timeouts,
    on_admin_decline_command,
    on_admin_help_command,
    on_admin_list_command,
    on_admin_relay_reply,
    on_admin_reply_command,
    on_admin_stats_command,
    on_admin_transcript_command,
    on_chat_member_updated,
    on_join_request,
    on_user_dm_reply,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper command (unchanged)
# ---------------------------------------------------------------------------
async def cmd_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Helper command /chat_id to easily find the ID of your Admin channel or group."""
    chat = update.effective_chat
    if chat:
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"📌 The Chat ID for '{chat.title or chat.type}' is:\n`{chat.id}`",
            parse_mode="Markdown",
        )
        logger.info("Chat ID requested for %s -> %s", chat.title, chat.id)


# ---------------------------------------------------------------------------
# Build PTB Application (no updater — we handle updates ourselves via Flask)
# ---------------------------------------------------------------------------
def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Fill it in your .env file.")

    database.init_db()

    app = Application.builder().token(BOT_TOKEN).updater(None).build()

    # Register all handlers (identical to the old bot.py)
    app.add_handler(CommandHandler("chat_id", cmd_chat_id))
    app.add_handler(CommandHandler("reply", on_admin_reply_command))
    app.add_handler(CommandHandler("decline", on_admin_decline_command))
    app.add_handler(CommandHandler("stats", on_admin_stats_command))
    app.add_handler(CommandHandler("list", on_admin_list_command))
    app.add_handler(CommandHandler("transcript", on_admin_transcript_command))
    app.add_handler(CommandHandler("help", on_admin_help_command))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(
        ChatMemberHandler(on_chat_member_updated, ChatMemberHandler.CHAT_MEMBER)
    )
    app.add_handler(
        MessageHandler(
            filters.REPLY & filters.TEXT & (~filters.COMMAND),
            on_admin_relay_reply,
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & (~filters.COMMAND),
            on_user_dm_reply,
        ),
        group=2,
    )

    return app


# ---------------------------------------------------------------------------
# Start the async event loop in a background thread
# ---------------------------------------------------------------------------
ptb_app = build_application()
loop = asyncio.new_event_loop()


async def _ptb_lifecycle():
    """Initialize and start PTB, then keep the loop alive forever."""
    async with ptb_app:
        await ptb_app.start()

        # Auto-register webhook with Telegram
        pa_domain = os.environ.get("PYTHONANYWHERE_DOMAIN", "")
        if pa_domain:
            webhook_url = f"https://{pa_domain}/webhook/{BOT_TOKEN}"
            await ptb_app.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES,
            )
            logger.info("Webhook set to %s", webhook_url)
        else:
            logger.warning(
                "PYTHONANYWHERE_DOMAIN not set. You must set the webhook manually "
                "or add PYTHONANYWHERE_DOMAIN to your .env file."
            )

        # Keep loop alive indefinitely
        await asyncio.Event().wait()
        await ptb_app.stop()


def _run_loop():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_ptb_lifecycle())


# Start the background thread (runs once when PythonAnywhere loads the WSGI app)
_bg_thread = threading.Thread(target=_run_loop, daemon=True)
_bg_thread.start()


# ---------------------------------------------------------------------------
# Flask WSGI Application (this is what PythonAnywhere serves 24/7)
# ---------------------------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Receives every Telegram update as JSON, converts it, and pushes it to PTB."""
    json_data = request.get_json(force=True)
    update = Update.de_json(data=json_data, bot=ptb_app.bot)

    # Push update to PTB's internal queue (thread-safe)
    asyncio.run_coroutine_threadsafe(ptb_app.update_queue.put(update), loop)

    # Passive timeout check: sweep for any 48-hour expired sessions
    asyncio.run_coroutine_threadsafe(check_expired_timeouts(ptb_app.bot), loop)

    return jsonify({"status": "ok"}), 200


@flask_app.route("/", methods=["GET"])
def health():
    return "Bot is alive!", 200


# ---------------------------------------------------------------------------
# Allow running locally with `python bot.py` for testing (uses polling)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Running in LOCAL DEV MODE with polling (not webhook).")

    # Cancel the background webhook thread — we'll use polling instead
    import signal
    signal.alarm(0)  # no-op, just to be safe

    # Build a fresh application with polling support
    from telegram.request import HTTPXRequest

    database.init_db()
    proxy_url = "http://proxy.server:3128" if os.environ.get("PYTHONANYWHERE_SITE") else None
    req = HTTPXRequest(proxy=proxy_url, connect_timeout=30.0, read_timeout=30.0)
    polling_app = Application.builder().token(BOT_TOKEN).request(req).build()

    polling_app.add_handler(CommandHandler("chat_id", cmd_chat_id))
    polling_app.add_handler(CommandHandler("reply", on_admin_reply_command))
    polling_app.add_handler(CommandHandler("decline", on_admin_decline_command))
    polling_app.add_handler(CommandHandler("stats", on_admin_stats_command))
    polling_app.add_handler(CommandHandler("list", on_admin_list_command))
    polling_app.add_handler(CommandHandler("transcript", on_admin_transcript_command))
    polling_app.add_handler(CommandHandler("help", on_admin_help_command))
    polling_app.add_handler(ChatJoinRequestHandler(on_join_request))
    polling_app.add_handler(
        ChatMemberHandler(on_chat_member_updated, ChatMemberHandler.CHAT_MEMBER)
    )
    polling_app.add_handler(
        MessageHandler(
            filters.REPLY & filters.TEXT & (~filters.COMMAND),
            on_admin_relay_reply,
        ),
        group=1,
    )
    polling_app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & (~filters.COMMAND),
            on_user_dm_reply,
        ),
        group=2,
    )
    logger.info("Bot running in polling mode. Ctrl+C to stop.")
    polling_app.run_polling(allowed_updates=Update.ALL_TYPES)
