import asyncio
import logging
import os

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
# Initialize PTB once at module load (no threads needed)
# ---------------------------------------------------------------------------
ptb_app = build_application()
_loop = asyncio.new_event_loop()
_loop.run_until_complete(ptb_app.initialize())
logger.info("PTB Application initialized.")

# Auto-register webhook with Telegram on startup
webhook_domain = os.environ.get("WEBHOOK_DOMAIN", "")
if webhook_domain:
    webhook_url = f"https://{webhook_domain}/webhook/{BOT_TOKEN}"
    _loop.run_until_complete(
        ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    )
    logger.info("Webhook set to %s", webhook_url)
else:
    logger.warning("WEBHOOK_DOMAIN not set. Set it in your .env file.")


# ---------------------------------------------------------------------------
# Flask WSGI Application (this is what PythonAnywhere serves 24/7)
# ---------------------------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Receives every Telegram update as JSON, processes it synchronously, and returns."""
    json_data = request.get_json(force=True)
    update = Update.de_json(data=json_data, bot=ptb_app.bot)

    # Process the update synchronously (no threads)
    _loop.run_until_complete(ptb_app.process_update(update))

    # Passive timeout check: sweep for any 48-hour expired sessions
    _loop.run_until_complete(check_expired_timeouts(ptb_app.bot))

    return jsonify({"status": "ok"}), 200


@flask_app.route("/", methods=["GET"])
def health():
    return "Bot is alive!", 200


# ---------------------------------------------------------------------------
# Allow running locally with `python bot.py` for testing (uses polling)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Running in LOCAL DEV MODE with polling (not webhook).")

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
