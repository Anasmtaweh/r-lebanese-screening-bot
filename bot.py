import logging
import os
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
from telegram.request import HTTPXRequest

import database
from config import BOT_TOKEN
from handlers import (
    on_admin_decline_command,
    on_admin_list_command,
    on_admin_relay_reply,
    on_admin_reply_command,
    on_admin_stats_command,
    on_chat_member_updated,
    on_join_request,
    on_user_dm_reply,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
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


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Fill it in your .env file.")

    # 1. Initialize SQLite database
    database.init_db()

    # 2. Create HTTPXRequest with increased 30s timeouts and PythonAnywhere Proxy
    # PythonAnywhere free tier requires outgoing traffic to route through their proxy.
    proxy_url = "http://proxy.server:3128" if os.environ.get("PYTHONANYWHERE_SITE") else None
    request = HTTPXRequest(proxy=proxy_url, connect_timeout=30.0, read_timeout=30.0)

    # 3. Build Application with JobQueue enabled
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # 4. Register handlers
    # Helper Command: /chat_id (to easily get your Admin Channel ID)
    app.add_handler(CommandHandler("chat_id", cmd_chat_id))
    app.add_handler(CommandHandler("reply", on_admin_reply_command))
    app.add_handler(CommandHandler("decline", on_admin_decline_command))
    app.add_handler(CommandHandler("stats", on_admin_stats_command))
    app.add_handler(CommandHandler("list", on_admin_list_command))


    # Join Request Handler
    app.add_handler(ChatJoinRequestHandler(on_join_request))

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

    logger.info(
        "R/lebanese Screening Bot is running. Waiting for join requests... (Ctrl+C to stop)"
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
