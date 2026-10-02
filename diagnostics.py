import logging
import time
import html
import re
from typing import Dict
from telegram import Update, Chat
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS, ADMIN_CHAT_ID
from evaluator import AnswerEvaluator

logger = logging.getLogger(__name__)

# Dedicated evaluator instance for diagnostics
_evaluator = AnswerEvaluator()

# Cooldown tracking: admin_id -> last_timestamp
_last_run_timestamp: Dict[int, float] = {}
COOLDOWN_SECONDS = 30.0


def _is_authorized(update: Update) -> bool:
    """
    Security Gate:
    1. Must be an explicitly whitelisted Admin user ID.
    2. Must be called in the official Admin chat OR in a private DM with the bot.
       (Never allowed in public groups or unauthorized channels).
    """
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False

    # 1. Whitelist check
    if user.id not in ADMIN_USER_IDS:
        logger.warning("UNAUTHORIZED /test_ai attempt by user_id=%s (chat_id=%s)", user.id, chat.id)
        return False

    # 2. Location restriction
    is_private = chat.type == Chat.PRIVATE
    is_admin_channel = str(chat.id) == str(ADMIN_CHAT_ID)
    if not (is_private or is_admin_channel):
        logger.warning("Admin user %s attempted /test_ai in unauthorized location: chat_id=%s", user.id, chat.id)
        return False

    return True


def _sanitize_error(error_str: str) -> str:
    """
    Redacts any sensitive tokens, API keys, or private URLs from error messages.
    """
    # Redact potential keys (gsk_*, AIza*, etc.)
    clean = re.sub(r'gsk_[A-Za-z0-9_-]+', 'gsk_***[REDACTED]***', error_str)
    clean = re.sub(r'AIza[A-Za-z0-9_-]+', 'AIza***[REDACTED]***', clean)
    clean = re.sub(r'Bearer\s+[A-Za-z0-9_\-\.]+', 'Bearer ***[REDACTED]***', clean)
    return html.escape(clean)


async def on_admin_test_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Isolated, hardened admin diagnostics command: /test_ai
    Pings both Gemini and Groq live from Render to verify operational status.
    """
    message = update.effective_message
    if not message:
        return

    # Security Verification
    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    now = time.time()

    # Rate Limiting Cooldown Gate
    last_run = _last_run_timestamp.get(user_id, 0.0)
    if (now - last_run) < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last_run))
        await message.reply_text(f"⏳ Please wait {remaining}s before running /test_ai again.")
        return

    _last_run_timestamp[user_id] = now
    logger.info("Admin %s initiated /test_ai diagnostics.", user_id)

    status_msg = await message.reply_text(
        "🧪 <b>Testing AI providers from Render...</b>\nPlease wait a moment.",
        parse_mode="HTML"
    )

    test_input = "Yes I am Lebanese from Beirut, 24 years old, found via Reddit r/lebanon, want to join to chat about local news."

    # 1. Test Groq
    if not _evaluator.groq_api_key:
        groq_result_text = "❌ <b>GROQ:</b> No <code>GROQ_API_KEY</code> or <code>AI_API_KEY</code> configured."
    else:
        t0 = time.perf_counter()
        try:
            res, feedback = await _evaluator.evaluate_with_groq(test_input)
            latency = time.perf_counter() - t0
            model_disp = html.escape(_evaluator.last_groq_model or "Groq")
            res_disp = html.escape(str(res))
            groq_result_text = (
                f"🟢 <b>GROQ: ONLINE</b>\n"
                f"• Latency: <code>{latency:.2f}s</code>\n"
                f"• Output: <code>{res_disp}</code>\n"
                f"• Model: <code>{model_disp}</code>"
            )
        except Exception as e:
            latency = time.perf_counter() - t0
            err_disp = _sanitize_error(str(e))
            groq_result_text = (
                f"🔴 <b>GROQ: FAILED</b>\n"
                f"• Latency: <code>{latency:.2f}s</code>\n"
                f"• Error: <pre>{err_disp}</pre>"
            )

    # 2. Test Gemini
    if not _evaluator.gemini_api_key:
        gemini_result_text = "❌ <b>GEMINI:</b> No <code>GEMINI_API_KEY</code> configured."
    else:
        t0 = time.perf_counter()
        try:
            res, feedback = await _evaluator.evaluate_with_gemini(test_input)
            latency = time.perf_counter() - t0
            model_disp = html.escape(_evaluator.last_gemini_model or "Gemini")
            res_disp = html.escape(str(res))
            gemini_result_text = (
                f"🟢 <b>GEMINI: ONLINE</b>\n"
                f"• Latency: <code>{latency:.2f}s</code>\n"
                f"• Output: <code>{res_disp}</code>\n"
                f"• Model: <code>{model_disp}</code>"
            )
        except Exception as e:
            latency = time.perf_counter() - t0
            err_disp = _sanitize_error(str(e))
            gemini_result_text = (
                f"🔴 <b>GEMINI: FAILED</b>\n"
                f"• Latency: <code>{latency:.2f}s</code>\n"
                f"• Error: <pre>{err_disp}</pre>"
            )

    report = (
        "🤖 <b>Live AI Diagnostics (from Render)</b>\n\n"
        f"{groq_result_text}\n\n"
        f"{gemini_result_text}"
    )

    try:
        await status_msg.edit_text(report, parse_mode="HTML")
    except TelegramError as e:
        logger.warning("HTML edit failed in /test_ai (%s), falling back to raw plain text.", e)
        plain_report = re.sub(r'<[^>]+>', '', report)
        await status_msg.edit_text(plain_report, parse_mode=None)
