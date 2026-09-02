import logging
import re
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from telegram import Chat, ChatMember, ChatMemberUpdated, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import database
from config import (
    ADMIN_CHAT_ID,
    ADMIN_USER_IDS,
    SCREENING_QUESTIONS_EN,
    SCREENING_QUESTIONS_AR,
    SCREENING_TIMEOUT_SECONDS,
    STATUS_APPROVED,
    STATUS_DECLINED,
    STATUS_DISMISSED,
    STATUS_PARTIAL,
    STATUS_PASSED_TO_ADMINS,
    STATUS_PENDING,
    STATUS_AWAITING_USER_REPLY,
)
from evaluator import (
    AnswerEvaluator,
    RESULT_INCOMPLETE,
    RESULT_SATISFACTORY,
    RESULT_UNSATISFACTORY,
    RESULT_JUNK,
)

logger = logging.getLogger(__name__)
evaluator = AnswerEvaluator()


def _safe_md(text: str) -> str:
    """Escapes Markdown formatting characters from user inputs to prevent parse errors."""
    if not text:
        return ""
    return str(text).replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')


def _is_admin(update: Update) -> bool:
    """Returns True only if the message sender's Telegram user ID is in the ADMIN_USER_IDS whitelist."""
    user = update.effective_user
    if not user:
        return False
    return user.id in ADMIN_USER_IDS


def _format_user_string(target_user_id: int) -> str:
    """Helper to return a string like 'John (@john123) (ID: 12345)' if metadata exists."""
    session = database.get_session(target_user_id)
    if not session:
        return str(target_user_id)
    try:
        meta = json.loads(session.get("user_metadata_json") or "{}")
        name = _safe_md(meta.get("full_name") or "Unknown")
        username = f" (@{_safe_md(meta.get('username'))})" if meta.get("username") else ""
        return f"{name}{username} (ID: {target_user_id})"
    except Exception:
        return str(target_user_id)


async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Helper to send alerts or reports to the ADMIN_CHAT_ID if configured."""
    if not ADMIN_CHAT_ID:
        logger.info("[ADMIN_NOTIFY_SKIP] ADMIN_CHAT_ID not set. Message: %s", text)
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")
    except TelegramError as e:
        logger.warning("Failed to send admin notification: %s", e)


async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered when a user requests to join the chat.
    1. Checks if user has previous history (declined before, joined & left, etc.).
    2. Resets/Creates their screening session in SQLite.
    3. Sends the screening questions paragraph via DM even if they applied before.
    4. Schedules a 48-hour timeout job in JobQueue.
    5. Alerts admins that screening has started, including their past history badge.
    """
    request = update.chat_join_request
    if not request:
        return

    user = request.from_user
    chat = request.chat

    logger.info(
        "JOIN REQUEST -> chat_id=%s | user_id=%s username=@%s name=%s",
        chat.id,
        user.id,
        user.username,
        user.full_name,
    )

    # 1. Check permanent history summary
    history_summary = database.format_user_history_summary(user.id, chat.id)
    history_block = f"\n\n{history_summary}" if history_summary else "\n\n✨ First-time applicant."

    # 1.5. Debounce check: prevent duplicate DMs if Telegram retries a webhook during cold start
    existing_session = database.get_session(user.id)
    if existing_session and existing_session.get("status") in [STATUS_PENDING, STATUS_PARTIAL]:
        updated_at = existing_session.get("updated_at")
        if updated_at:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            # If request is within 5 minutes, it's a webhook duplicate, ignore it
            if datetime.now(timezone.utc) - updated_at < timedelta(minutes=5):
                logger.info(f"Ignoring duplicate join request because user {user.id} recently started.")
                return
        
        # If > 5 minutes, it's a deliberate re-join. Record history and restart them.
        database.add_user_history(user.id, chat.id, "RESTARTED_SCREENING", "User cancelled and re-sent join request.")

    # 2. Initialize SQLite session with user metadata for future AI training
    user_metadata = {
        "username": user.username,
        "full_name": user.full_name,
        "is_premium": user.is_premium,
        "language_code": user.language_code
    }
    database.add_or_reset_session(user.id, chat.id, user_metadata)

    # 3. Send language selection intro message
    intro_text = "Hello! I am the automated screening bot for R/lebanese. Please choose your language to continue:\n\nمرحباً! أنا بوت الفحص الآلي لمجتمع R/lebanese. الرجاء اختيار اللغة للمتابعة:"
    keyboard = [
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇱🇧 عربي", callback_data="lang_ar"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        sent_msg = await context.bot.send_message(
            chat_id=user.id, 
            text=intro_text, 
            reply_markup=reply_markup
        )
        database.add_bot_message_id(user.id, sent_msg.message_id)
        logger.info("Sent language selection DM to user %s (message_id=%s)", user.id, sent_msg.message_id)
    except TelegramError as e:
        logger.error("Could not send screening DM to user %s: %s", user.id, e)
        safe_username = _safe_md(user.username)
        safe_name = _safe_md(user.full_name)
        username_str = f"(@{safe_username}) " if safe_username else ""
        
        # Silently decline them immediately if they block bots or require payment
        try:
            await context.bot.decline_chat_join_request(chat_id=chat.id, user_id=user.id)
        except TelegramError:
            pass
            
        database.update_session_status(user.id, STATUS_DISMISSED)
        database.add_user_history(user.id, chat.id, "DECLINED_NO_DM", f"Auto-declined: Could not DM ({e})")
        
        await send_admin_notification(
            context,
            f"🚫 *Auto-Declined: Cannot Send DM*\n"
            f"👤 User {safe_name} {username_str}| ID: `{user.id}`\n"
            f"Reason: `Telegram Error - {e}`\n"
            f"*(They likely blocked the bot or require paid Telegram Stars for PMs)*"
        )
        return

    # Remove old in-memory timeout job logic (handled by cron job now)
    
    safe_username = _safe_md(user.username)
    safe_name = _safe_md(user.full_name)
    username_str = f"(@{safe_username}) " if safe_username else ""
    # 5. Notify Admins with clean, short notification
    await send_admin_notification(
        context,
        f"✉️ Screening DM sent successfully to User: {safe_name} {username_str}| ID: `{user.id}`\n"
        f"{history_block}\n"
        f"48-hour rolling timer started.",
    )


async def on_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the user clicking a language button."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = update.effective_user
    lang = query.data.split("_")[1]  # 'en' or 'ar'
    
    # Save the language preference in the database
    database.update_session_language(user.id, lang)

    # Edit the intro message to show the actual questions in the chosen language
    questions = SCREENING_QUESTIONS_AR if lang == "ar" else SCREENING_QUESTIONS_EN
    
    try:
        await query.edit_message_text(text=questions)
        logger.info("User %s selected language '%s'", user.id, lang)
    except Exception as e:
        logger.error("Failed to edit language message for user %s: %s", user.id, e)


async def on_user_dm_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered when a user sends a private message to the bot.
    1. Records reply in transcript and increments attempt count.
    2. Resets the rolling 48-hour timeout from this new message.
    3. Evaluates reply:
       - SATISFACTORY -> Alerts admins immediately.
       - INCOMPLETE (Attempt 1) -> Silently sends follow-up prompt to user without alerting admins.
       - INCOMPLETE (Attempt 2+) -> Sends full conversation transcript to admins to take over.
       - UNSATISFACTORY -> Sends flagged report to admins for manual review (no auto-decline).
    """
    if not update.message or not update.message.text:
        return
    if update.effective_chat.type != Chat.PRIVATE:
        return

    user = update.effective_user
    user_text = update.message.text
    session = database.get_active_session(user.id)

    if not session:
        await update.message.reply_text(
            "You do not have any pending join request screenings at this time."
        )
        return

    # Fix: Stop processing messages after user already passed screening
    if session["status"] == STATUS_APPROVED:
        await update.message.reply_text(
            "Your answers have already been submitted and are under review by R/lebanese admins. "
            "Please wait for an admin to get back to you!"
        )
        return
        
    if session["status"] == STATUS_PASSED_TO_ADMINS:
        await update.message.reply_text(
            "Your answers have already been submitted and are under review by R/lebanese admins. "
            "Please wait for an admin to get back to you!"
        )
        return

    chat_id = session["chat_id"]
    database.add_to_transcript(user.id, "user", user_text)
    attempt_count = database.increment_attempt_count(user.id)

    # If the user is replying to a custom admin question:
    if session["status"] == STATUS_AWAITING_USER_REPLY:
        # Pause the timer by putting them back in the admin's court
        database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"💬 User Reply from {user.name} (ID: `{user.id}`):\n\n「{user_text}」\n\n💡 Use /reply {user.id} <msg> to reply back.",
            parse_mode="Markdown"
        )
        return

    # Rolling 48-hour timer is automatically reset because database.increment_attempt_count 
    # and update_session_status update the 'updated_at' timestamp, which the cron job checks.

    # Fetch chosen language
    meta = json.loads(session["user_metadata_json"] or "{}")
    lang_code = meta.get("language_code", "en")

    # Fix: Evaluate the FULL combined transcript, not just the latest message
    combined_replies = database.get_all_user_replies_combined(user.id)
    res_type, feedback = evaluator.evaluate(combined_replies, language_code=lang_code)
    logger.info("User %s reply attempt #%s evaluated as %s", user.id, attempt_count, res_type)

    history_summary = database.format_user_history_summary(user.id, chat_id)
    history_block = f"\n\n{history_summary}" if history_summary else ""

    if res_type == RESULT_SATISFACTORY:
        database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS, answers_text=user_text)
        database.add_user_history(user.id, chat_id, "PASSED_SCREENING", "Answered all questions satisfactorily")
        await update.message.reply_text(
            "Thank you! Your answers have been received and submitted to R/lebanese admins for review."
        )

        transcript_text = database.get_transcript_summary(user.id)
        safe_username = _safe_md(user.username)
        safe_name = _safe_md(user.full_name)
        username_str = f"(@{safe_username}) " if safe_username else ""
        admin_report = (
            f"📋 *Satisfactory Screening Reply*\n"
            f"👤 {safe_name} {username_str}| ID: `{user.id}`\n"
            f"{transcript_text}\n\n"
            f"💡 Use `/reply {user.id} <msg>` or approve/decline in Telegram."
        )
        await send_admin_notification(context, admin_report)

    elif res_type == RESULT_INCOMPLETE:
        if attempt_count < 3:
            database.update_session_status(user.id, STATUS_PARTIAL, answers_text=user_text)
            try:
                follow_up_msg = await update.message.reply_text(feedback)
                database.add_bot_message_id(user.id, follow_up_msg.message_id)
                database.add_to_transcript(user.id, "bot", feedback)
            except TelegramError as e:
                logger.error("Could not send follow-up prompt to %s: %s", user.id, e)
            logger.info("Attempt %s incomplete for user %s. Sent silent follow-up prompt.", attempt_count, user.id)
        else:
            # On 3rd or later incomplete attempt, lock the session and push transcript to admins
            database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS, answers_text=user_text)
            database.add_to_transcript(user.id, "bot", "*(Interview concluded due to incomplete answers)*")
            await update.message.reply_text(
                "Thank you! Your answers have been received and submitted to R/lebanese admins for review."
            )
            
            transcript_text = database.get_transcript_summary(user.id)
            safe_username = _safe_md(user.username)
            safe_name = _safe_md(user.full_name)
            username_str = f"(@{safe_username}) " if safe_username else ""
            await send_admin_notification(
                context,
                f"📋 *Screening Report (Needs Admin Attention)*\n"
                f"👤 {safe_name} {username_str}| ID: `{user.id}`\n"
                f"{transcript_text}\n\n"
                f"💡 Use `/reply {user.id} <msg>` or `/decline {user.id}`.",
            )

    elif res_type == RESULT_UNSATISFACTORY:
        # DO NOT auto-decline! Send for manual admin review
        database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS, answers_text=user_text)
        database.add_to_transcript(user.id, "bot", "⚠️ Flagged by screening check")
        transcript_text = database.get_transcript_summary(user.id)
        safe_username = _safe_md(user.username)
        safe_name = _safe_md(user.full_name)
        username_str = f"(@{safe_username}) " if safe_username else ""
        await send_admin_notification(
            context,
            f"⚠️ *Flagged Screening Reply (Review Needed)*\n"
            f"👤 {safe_name} {username_str}| ID: `{user.id}`\n"
            f"{transcript_text}\n\n"
            f"💡 Please review. Use `/reply {user.id} <msg>` or `/decline {user.id}`.",
        )

    elif res_type == RESULT_JUNK:
        # Silently decline on the spot! Do NOT send any DM to the user.
        database.update_session_status(user.id, STATUS_DECLINED, answers_text=user_text)
        database.add_user_history(user.id, chat_id, "DECLINED_JUNK", "Declined on the spot for junk/spam reply")

        # Silently decline their Telegram join request
        try:
            await context.bot.decline_chat_join_request(chat_id=chat_id, user_id=user.id)
            logger.info("Silently declined join request for user %s due to JUNK reply", user.id)
        except TelegramError as e:
            logger.error("Error declining join request for user %s: %s", user.id, e)

        # Notify Admins with the user's junk reply
        safe_username = _safe_md(user.username)
        safe_name = _safe_md(user.full_name)
        username_str = f"(@{safe_username}) " if safe_username else ""
        await send_admin_notification(
            context,
            f"🗑️ *Automatically Declined: Junk Reply*\n"
            f"👤 {safe_name} {username_str}| ID: `{user.id}`\n\n"
            f"💬 Their Reply: \"{user_text}\"",
        )



async def on_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered when a user's membership status in the chat changes (e.g. joined or left).
    Records APPROVED_JOINED or LEFT_GROUP in permanent user history.
    """
    chat_member = update.chat_member
    if not chat_member:
        return

    user = chat_member.new_chat_member.user
    chat = update.effective_chat
    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status

    # User joined / was approved
    if old_status in (ChatMember.LEFT, ChatMember.BANNED) and new_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        database.add_user_history(user.id, chat.id, "APPROVED_JOINED", "User joined the group")
        database.update_session_status(user.id, STATUS_APPROVED)
        await _delete_bot_messages(context, user.id)
        logger.info("Recorded history: User %s joined group %s and session approved", user.id, chat.id)

    # User left / was kicked
    elif old_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR) and new_status in (ChatMember.LEFT, ChatMember.BANNED):
        database.add_user_history(user.id, chat.id, "LEFT_GROUP", "User left or was removed from group")
        logger.info("Recorded history: User %s left group %s", user.id, chat.id)


async def on_admin_relay_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered when an admin replies to a bot message inside ADMIN_CHAT_ID.
    Extracts the user ID and relays the admin's reply back to the user's DM.
    """
    if not update.message or not update.message.reply_to_message:
        return

    # Security: Only allow authorized admins
    if not _is_admin(update):
        return

    replied_text = update.message.reply_to_message.text or ""
    # Extract user ID from text like "ID: 123456789" or "ID: `123456789`"
    match = re.search(r"ID:\s*`?(\d+)`?", replied_text)
    if not match:
        return

    target_user_id = int(match.group(1))
    admin_text = update.message.text

    try:
        sent_msg = await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💬 Message from R/lebanese Admin:\n\n{admin_text}",
        )
        keyboard = [[InlineKeyboardButton("Undo ↩️", callback_data=f"undo_{target_user_id}_{sent_msg.message_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✅ Your message has been relayed to user ID {target_user_id}.",
            reply_markup=reply_markup
        )
        logger.info("Admin relayed message to user %s", target_user_id)
        # Start 48-hour timer for user to reply
        database.update_session_status(target_user_id, STATUS_AWAITING_USER_REPLY)
    except TelegramError as e:
        logger.error("Could not relay message to user %s: %s", target_user_id, e)
        await update.message.reply_text(
            f"❌ Failed to send message to user ID {target_user_id}: {e}"
        )

async def cleanup_expired_sessions_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered by JobQueue on a repeating interval (e.g. 10 minutes).
    Fetches all pending/partial sessions from the database where updated_at is older
    than SCREENING_TIMEOUT_SECONDS. Dismisses them.
    """
    try:
        expired_sessions = database.get_expired_sessions(SCREENING_TIMEOUT_SECONDS)
    except Exception as e:
        logger.error("Error fetching expired sessions: %s", e)
        return

    if not expired_sessions:
        return

    logger.info("Cron found %s expired sessions. Processing...", len(expired_sessions))

    for session in expired_sessions:
        user_id = session["user_id"]
        chat_id = session["chat_id"]
        
        # Get username safely
        meta = {}
        try:
            meta = json.loads(session.get("user_metadata_json") or "{}")
        except:
            pass
        user_name = _safe_md(meta.get("full_name")) or str(user_id)
        
        logger.info("Timeout fired for user %s (%s). Auto-dismissing.", user_id, user_name)
        database.update_session_status(user_id, STATUS_DISMISSED)
        database.add_user_history(user_id, chat_id, "DISMISSED_TIMEOUT", "Did not reply within 48 hours")

        # Decline join request
        try:
            await context.bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            logger.info("Auto-declined join request for user %s on timeout", user_id)
        except TelegramError as e:
            logger.error("Error declining join request on timeout for %s: %s", user_id, e)

        # Delete bot screening messages
        await _delete_bot_messages(context, user_id)

        await send_admin_notification(
            context,
            f"⏳ *48-Hour Timeout*: User {user_name} (ID: `{user_id}`) did not reply in time.\n"
            f"Their join request was automatically DECLINED and screening DM messages deleted.",
        )


async def _delete_bot_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Helper to delete all recorded bot messages sent to user_id."""
    msg_ids = database.get_bot_message_ids(user_id)
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            logger.info("Deleted bot message %s for user %s", msg_id, user_id)
        except TelegramError as e:
            logger.debug("Could not delete message %s for user %s: %s", msg_id, user_id, e)


async def on_admin_reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /reply <user_id> <message text>
    Sends a direct DM to the applicant and confirms to the admin.
    """
    if not update.message or not update.message.text:
        return
    if not _is_admin(update):
        return

    args = context.args or []
    if len(args) < 2 or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /reply <user_id> <message>")
        return

    target_user_id = int(args[0])
    msg_text = " ".join(args[1:])
    try:
        sent_msg = await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💬 Message from R/lebanese Admin:\n\n{msg_text}",
        )
        keyboard = [[InlineKeyboardButton("Undo ↩️", callback_data=f"undo_{target_user_id}_{sent_msg.message_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        user_str = _format_user_string(target_user_id)
        await update.message.reply_text(
            f"✅ Sent DM to user {user_str}.",
            reply_markup=reply_markup
        )
        logger.info("Admin command /reply sent to %s", target_user_id)
        # Start 48-hour timer for user to reply
        database.update_session_status(target_user_id, STATUS_AWAITING_USER_REPLY)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not send DM to {target_user_id}: {e}")


async def on_admin_decline_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /decline <user_id> [reason]
    Sends a decline DM to the applicant, declines their join request, and deletes screening DMs.
    """
    message = update.effective_message
    if not message or not message.text:
        return
    if not _is_admin(update):
        return

    args = context.args or []
    if len(args) < 1 or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /decline <user_id> [optional reason]")
        return

    target_user_id = int(args[0])
    reason = " ".join(args[1:]) if len(args) > 1 else "Your application did not meet the screening requirements."

    session = database.get_session(target_user_id)
    chat_id = session["chat_id"] if session else int(ADMIN_CHAT_ID or 0)

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"🚫 R/lebanese Application Update:\n\n{reason}",
        )
    except TelegramError:
        pass

    if chat_id:
        try:
            await context.bot.decline_chat_join_request(chat_id=chat_id, user_id=target_user_id)
        except TelegramError:
            pass
        database.add_user_history(target_user_id, chat_id, "DISMISSED_ADMIN", reason)

    await _delete_bot_messages(context, target_user_id)
    database.update_session_status(target_user_id, STATUS_DISMISSED)
    user_str = _format_user_string(target_user_id)
    await update.message.reply_text(f"🚫 User {user_str} declined & messages deleted.")


async def on_admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /stats
    Shows overall screening statistics.
    """
    message = update.effective_message
    if not message:
        return
    if not _is_admin(update):
        return

    stats = database.get_screening_stats()
    msg = (
        "📊 **R/lebanese Screening Statistics**\n\n"
        f"• Total Join Requests: {stats['total_requests']}\n"
        f"• Passed Screening: {stats['passed']}\n"
        f"• Accepted into Group: {stats['accepted']}\n"
        f"• Declined (Junk Reply): {stats['declined_junk']}\n"
        f"• Declined (48h Timeout): {stats['timeout']}\n"
        f"• Currently In Screening: {stats['active']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def on_admin_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /list <passed|junk|timeout>
    Shows a list of the last 20 users who fall into that category.
    """
    message = update.effective_message
    if not message:
        return
    if not _is_admin(update):
        return

    args = context.args or []
    if len(args) < 1 or args[0].lower() not in ["passed", "junk", "timeout", "screening", "pending", "accepted"]:
        await update.message.reply_text("Usage: /list <passed|junk|timeout|pending|accepted>")
        return

    category = args[0].lower()
    if category in ("screening", "pending"):
        users = database.get_pending_users(limit=20)
    else:
        event_map = {
            "passed": "PASSED_SCREENING",
            "junk": "DECLINED_JUNK",
            "timeout": "DISMISSED_TIMEOUT",
            "accepted": "APPROVED_JOINED"
        }
        event_type = event_map[category]
        users = database.get_recent_users_by_event(event_type, limit=20)
    
    if not users:
        await update.message.reply_text(f"No users found in the '{category}' category.")
        return

    lines = [f"📋 Last 20 users in category: {category.upper()}"]
    for idx, u in enumerate(users, 1):
        uid = u["user_id"]
        date = str(u["created_at"])[:16]
        meta = u.get("metadata", {})
        name = meta.get("full_name") or "Unknown Name"
        username = f"(@{meta.get('username')})" if meta.get("username") else ""
        lines.append(f"{idx}. {name} {username} (ID: {uid}) - {date}")

    await update.message.reply_text("\n".join(lines))


async def on_admin_transcript_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /transcript <user_id>
    Shows the full stored conversation transcript between the bot and the user.
    """
    message = update.effective_message
    if not message or not message.text:
        return
    if not _is_admin(update):
        return

    args = context.args or []
    if len(args) < 1 or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /transcript <user_id>")
        return

    target_user_id = int(args[0])
    transcript_text = database.get_transcript_summary(target_user_id)
    
    if not transcript_text:
        await update.message.reply_text(f"No conversation transcript found for user ID {target_user_id}.")
        return
        
    user_str = _format_user_string(target_user_id)
    await update.message.reply_text(f"📄 Transcript for {user_str}:\n\n{transcript_text}")


async def on_admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /help
    Shows all available admin commands and how to use them.
    """
    message = update.effective_message
    if not message:
        return
    if not _is_admin(update):
        return

    help_text = (
        "🛠️ **R/lebanese Screening Bot - Admin Commands**\n\n"
        "**Analytics & Reports**\n"
        "• `/stats` - View overall screening numbers (passed, pending, declined, etc.)\n"
        "• `/list <category>` - View the last 20 users in a specific category.\n"
        "   *Examples:*\n"
        "   👉 `/list pending` (Users currently answering questions)\n"
        "   👉 `/list passed` (Users who passed successfully)\n"
        "   👉 `/list accepted` (Users formally accepted into the group)\n"
        "   👉 `/list junk` (Users declined for spam/junk)\n"
        "   👉 `/list timeout` (Users who didn't answer in 48h)\n"
        "• `/transcript <user_id>` - Read the exact private chat history between the bot and a specific user.\n\n"
        "**Manual Actions**\n"
        "• `/reply <user_id> <message>` - Send a custom DM to an applicant.\n"
        "   *Example:* `/reply 123456789 Please clarify your age.`\n"
        "• `/decline <user_id>` - Silently decline an applicant and delete their DM history.\n\n"
        "*(Note: You can also approve/decline users natively via Telegram's group management menu!)*"
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Callback handler for the [Undo] button on admin replies.
    Deletes the specific message from the user's DM.
    """
    query = update.callback_query
    if not query or not query.data:
        return

    # Security: only allow admins to undo
    if not _is_admin(update):
        await query.answer("Unauthorized.", show_alert=True)
        return

    # Extract user_id and msg_id from "undo_12345_67890"
    parts = query.data.split("_")
    if len(parts) != 3:
        await query.answer("Invalid undo data.")
        return
        
    target_user_id = int(parts[1])
    target_msg_id = int(parts[2])

    try:
        await context.bot.delete_message(chat_id=target_user_id, message_id=target_msg_id)
        await query.answer("Message deleted from user's DM.")
        await query.edit_message_text(f"🗑️ Message successfully undone/deleted from user {target_user_id}.")
        logger.info(f"Admin undone message {target_msg_id} for user {target_user_id}")
    except TelegramError as e:
        logger.error(f"Failed to undo message: {e}")
        await query.answer(f"Could not delete message: {e}", show_alert=True)
