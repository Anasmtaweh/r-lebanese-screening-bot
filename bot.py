import logging
import logging.handlers
import os
from telegram import Update
from telegram.ext import (
    Application,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest
import httpx

import database
from config import BOT_TOKEN, HEALTHCHECK_URL
from handlers import (
    on_admin_decline_command,
    on_admin_help_command,
    on_admin_list_command,
    on_admin_relay_reply,
    on_admin_reply_command,
    on_admin_stats_command,
    on_admin_transcript_command,
    on_chat_member_updated,
    on_join_request,
    on_language_selection,
    on_user_dm_reply,
)

# Persistent file logging — survives crashes, always available on PythonAnywhere
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screening_bot.log")
log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3  # 5MB per file, keep 3 backups
)
log_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[log_handler, console_handler])
logger = logging.getLogger(__name__)


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


async def ping_healthcheck(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pings Healthchecks.io every 5 minutes to prove the bot is alive."""
    if not HEALTHCHECK_URL:
        return
        
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.get(HEALTHCHECK_URL)
            logger.debug("Successfully pinged Healthchecks.io")
    except Exception as e:
        logger.warning("Failed to ping Healthchecks.io: %s", e)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Fill it in your .env file.")

    # 1. Initialize PostgreSQL database
    database.init_db()

    # 2. Create HTTPXRequest with increased 30s timeouts
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)

    # 3. Build Application with JobQueue enabled
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # 4. Register handlers
    # Helper Command: /chat_id (to easily get your Admin Channel ID)
    app.add_handler(CommandHandler("chat_id", cmd_chat_id))
    app.add_handler(CommandHandler("reply", on_admin_reply_command))
    app.add_handler(CommandHandler("decline", on_admin_decline_command))
    app.add_handler(CommandHandler("stats", on_admin_stats_command))
    app.add_handler(CommandHandler("list", on_admin_list_command))
    app.add_handler(CommandHandler("transcript", on_admin_transcript_command))
    app.add_handler(CommandHandler("help", on_admin_help_command))


    # Join Request Handler
    app.add_handler(ChatJoinRequestHandler(on_join_request))

    # Language Selection Callback Handler
    app.add_handler(CallbackQueryHandler(on_language_selection, pattern="^lang_"))

    # Chat Member Status Handler (Tracks when users join or leave the group)
    app.add_handler(
        ChatMemberHandler(on_chat_member_updated, ChatMemberHandler.CHAT_MEMBER)
    )

    # Admin Relay Handler (Triggered when replying to a bot notification in Admin chat)
    app.add_handler(
        MessageHandler(
            filters.REPLY & filters.TEXT & (~filters.COMMAND),
            on_admin_relay_reply,
        ),
        group=1,
    )

    # User DM Reply Handler (Triggered when a user sends an answer in private DM)
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & (~filters.COMMAND),
            on_user_dm_reply,
        ),
        group=2,
    )

    # Start Healthcheck heartbeat job (runs every 300 seconds = 5 minutes)
    if app.job_queue:
        app.job_queue.run_repeating(ping_healthcheck, interval=300, first=10)
        logger.info("Healthchecks.io heartbeat scheduled (5 min intervals)")

    logger.info("R/lebanese Screening Bot is ready.")

    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        port = int(os.environ.get("PORT", "8443"))
        logger.info(f"Running Webhook on port {port} for {render_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=f"{render_url}"
        )
    else:
        logger.info("Running Polling mode (Ctrl+C to stop)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
