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
    DEVELOPER_CHAT_ID,
    SCREENING_QUESTIONS_EN,
    SCREENING_QUESTIONS_AR,
    SCREENING_TIMEOUT_SECONDS,
    PROBATION_TIMEOUT_SECONDS,
    STATUS_APPROVED,
    STATUS_DECLINED,
    STATUS_DISMISSED,
    STATUS_PARTIAL,
    STATUS_PASSED_TO_ADMINS,
    STATUS_PENDING,
    STATUS_AWAITING_USER_REPLY,
    STATUS_PROBATION,
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

# In-memory cache of probation user IDs to avoid hitting the DB on every group message
_probation_cache: set = set()

def load_probation_cache() -> None:
    """Loads all probation user IDs from the database into the in-memory cache."""
    global _probation_cache
    _probation_cache = set(database.get_probation_user_ids())
    logger.info("Loaded %d users into probation cache.", len(_probation_cache))


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




def _extract_target_id(update, context) -> int:
    """Extracts the target user_id from either command args or the replied-to message."""
    args = context.args or []
    if args and args[0].lstrip("-").isdigit():
        return int(args[0])
    
    if update.message and update.message.reply_to_message and update.message.reply_to_message.text:
        import re
        match = re.search(r"ID:\s*`?(\d+)`?", update.message.reply_to_message.text)
        if match:
            return int(match.group(1))
    return None


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
    2. Resets/Creates their screening session in the database.
    3. Sends the screening questions paragraph via DM even if they applied before.
    4. The cron job handles the 48-hour timeout.
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
    if existing_session:
        updated_at = existing_session.get("updated_at")
        if updated_at:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            # If request is within 5 minutes, it's a webhook duplicate, ignore it
            if datetime.now(timezone.utc) - updated_at < timedelta(minutes=5):
                logger.info(f"Ignoring duplicate join request because user {user.id} recently interacted.")
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
    try:
        await query.answer()
    except Exception as e:
        logger.warning("Callback query.answer() failed (likely expired during cold start): %s", e)

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
            "You do not have any pending join request screenings at this time.\n"
            "ليس لديك أي طلب انضمام قيد المراجعة حالياً."
        )
        return

    # Fetch chosen language for localized responses
    meta = json.loads(session["user_metadata_json"] or "{}")
    lang_code = meta.get("language_code", "en")

    # Fix: If user was already approved and is in the group, tell them to use the group
    if session["status"] == STATUS_APPROVED:
        if lang_code == "ar":
            await update.message.reply_text(
                "لقد تم قبولك بالفعل! يرجى إرسال رسائلك داخل المجموعة. إذا كنت بحاجة إلى المساعدة، يمكنك التواصل مع المشرفين على الخاص."
            )
        else:
            await update.message.reply_text(
                "You have already been approved! Please send your messages in the group. If you need help, you may contact the admins privately."
            )
        return

    # Fix: If user is chatting with admins or waiting for review, forward their message
    if session["status"] == STATUS_PASSED_TO_ADMINS:
        safe_name = _safe_md(user.full_name) if user.full_name else str(user.id)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"💬 User Reply (Awaiting Review) from {safe_name} (ID: `{user.id}`):\n\n「{user_text}」\n\n💡 Use /reply {user.id} <msg> to reply back.",
            parse_mode="Markdown"
        )
        return

    # Fix: If user is on probation, just forward to admin but do NOT clear probation status
    if session["status"] == STATUS_PROBATION:
        safe_name = _safe_md(user.full_name) if user.full_name else str(user.id)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"💬 User Reply (On Probation) from {safe_name} (ID: `{user.id}`):\n\n「{user_text}」\n\n💡 They are still on probation.",
            parse_mode="Markdown"
        )
        return

    chat_id = session["chat_id"]
    database.add_to_transcript(user.id, "user", user_text)
    attempt_count = database.increment_attempt_count(user.id)

    # If the user is replying to a custom admin question:
    if session["status"] == STATUS_AWAITING_USER_REPLY:
        # Pause the timer by putting them back in the admin's court
        database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS)
        
        safe_name = _safe_md(user.name)
        safe_text = _safe_md(user_text)
        
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"💬 User Reply from {safe_name} (ID: `{user.id}`):\n\n「{safe_text}」\n\n💡 Use /reply {user.id} <msg> to reply back.",
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
    res_type, feedback, was_ai_used, ai_error_msg = await evaluator.evaluate(combined_replies, language_code=lang_code)
    logger.info("User %s reply attempt #%s evaluated as %s", user.id, attempt_count, res_type)
    
    if ai_error_msg and DEVELOPER_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=DEVELOPER_CHAT_ID,
                text=f"⚠️ *AI Evaluation Failed (Fallback Used)*\n\nUser ID: `{user.id}`\nError: `{ai_error_msg}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error("Could not notify developer of AI failure: %s", e)

    history_summary = database.format_user_history_summary(user.id, chat_id)
    history_block = f"\n\n{history_summary}" if history_summary else ""

    if res_type == RESULT_SATISFACTORY:
        database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS, answers_text=user_text)
        database.add_user_history(user.id, chat_id, "PASSED_SCREENING", "Answered all questions satisfactorily")
        if lang_code == "ar":
            await update.message.reply_text(
                "شكراً! تم استلام إجاباتك وإرسالها إلى إدارة R/lebanese للمراجعة."
            )
        else:
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
        max_attempts = 3 if was_ai_used else 2
        if attempt_count < max_attempts:
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
            if lang_code == "ar":
                await update.message.reply_text(
                    "شكراً! تم استلام إجاباتك وإرسالها إلى إدارة R/lebanese للمراجعة."
                )
            else:
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
        # SAFETY NET: On the first attempt, never auto-decline. Give them a chance
        # by downgrading JUNK to INCOMPLETE and re-asking the screening questions.
        if attempt_count <= 1:
            logger.info("JUNK on first attempt for user %s — downgrading to INCOMPLETE and re-asking questions.", user.id)
            database.update_session_status(user.id, STATUS_PARTIAL, answers_text=user_text)
            if lang_code == "ar":
                follow_up = (
                    "يرجى الإجابة على جميع الأسئلة الأربعة حتى تتم مراجعة طلبك:\n\n"
                    "1. هل أنت لبناني؟ إذا لا، من أي بلد أنت؟\n"
                    "2. هل عمرك 18 سنة أو أكثر؟\n"
                    "3. كيف عرفت عن السيرفر؟\n"
                    "4. لماذا تريد الانضمام إلى السيرفر؟"
                )
            else:
                follow_up = (
                    "Please answer all 4 screening questions so your request can be reviewed:\n\n"
                    "1. Are you Lebanese? If not, what country are you from?\n"
                    "2. Are you 18 or over?\n"
                    "3. How did you find out about our server?\n"
                    "4. Why are you interested in joining our server?"
                )
            try:
                follow_up_msg = await update.message.reply_text(follow_up)
                database.add_bot_message_id(user.id, follow_up_msg.message_id)
                database.add_to_transcript(user.id, "bot", follow_up)
            except TelegramError as e:
                logger.error("Could not send follow-up prompt to %s: %s", user.id, e)
        else:
            # 2nd+ junk attempt: silently decline on the spot. Do NOT send any DM to the user.
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
    Triggered when a user's membership status in the chat changes.
    Handles: joins, leaves, kicks, and manual approvals.
    Records events in permanent user history and manages probation state.
    """
    chat_member = update.chat_member
    if not chat_member:
        return

    user = chat_member.new_chat_member.user
    chat = update.effective_chat
    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status

    if old_status == new_status:
        return

    # User joined / was approved
    if old_status in (ChatMember.LEFT, ChatMember.BANNED) and new_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        database.add_user_history(user.id, chat.id, "APPROVED_JOINED", "User joined the group")
        # Don't override PROBATION status — admin may set it later
        session = database.get_session(user.id)
        if not session or session["status"] != STATUS_PROBATION:
            database.update_session_status(user.id, STATUS_APPROVED)
            _probation_cache.discard(user.id)
        await _delete_bot_messages(context, user.id)
        logger.info("Recorded history: User %s joined group %s and session approved", user.id, chat.id)

    # User left voluntarily
    elif old_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR) and new_status == ChatMember.LEFT:
        database.add_user_history(user.id, chat.id, "LEFT_GROUP", "User left the group manually.")
        database.update_session_status(user.id, STATUS_PENDING)
        _probation_cache.discard(user.id)
        logger.info("Recorded history: User %s left group %s", user.id, chat.id)

    # User was kicked / banned
    elif old_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR) and new_status == ChatMember.BANNED:
        database.add_user_history(user.id, chat.id, "MANUALLY_KICKED", "Kicked or banned by an admin or another bot.")
        database.update_session_status(user.id, STATUS_PENDING)
        _probation_cache.discard(user.id)
        logger.info("Recorded history: User %s was kicked from group %s", user.id, chat.id)


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
        # (Probation is handled separately via /probation_en and /probation_ar commands)
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

    # --- Probation timeout: 24 hours ---
    try:
        expired_probation = database.get_expired_probation_sessions(PROBATION_TIMEOUT_SECONDS)
    except Exception as e:
        logger.error("Error fetching expired probation sessions: %s", e)
        expired_probation = []

    if expired_probation:
        logger.info("Cron found %s expired probation sessions. Processing...", len(expired_probation))

    for session in expired_probation:
        user_id = session["user_id"]
        chat_id = session["chat_id"]

        meta = {}
        try:
            meta = json.loads(session.get("user_metadata_json") or "{}")
        except:
            pass
        user_name = _safe_md(meta.get("full_name")) or str(user_id)

        logger.info("Probation timeout for user %s (%s). Kicking.", user_id, user_name)
        database.update_session_status(user_id, STATUS_DISMISSED)
        _probation_cache.discard(user_id)
        database.add_user_history(user_id, chat_id, "KICKED_PROBATION", "Did not reply to admin in group within 7 days")

        # Kick from group (ban + unban = kick without permanent ban)
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
            logger.info("Kicked user %s from group %s (probation expired)", user_id, chat_id)
        except TelegramError as e:
            logger.error("Error kicking user %s from group: %s", user_id, e)

        await send_admin_notification(
            context,
            f"🚫 *1-Week Probation Expired*: User {user_name} (ID: `{user_id}`) did not reply to an admin in the group.\n"
            f"They have been automatically kicked.",
        )


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lightweight handler for group messages. Only checks the sender's user ID
    against probation users. Does NOT read, log, or store any message content.
    """
    if not update.effective_user:
        return

    user = update.effective_user

    # Quick cache check: is this user on probation?
    if user.id not in _probation_cache:
        return

    # User is in cache! Double check DB to be safe
    session = database.get_session(user.id)
    if not session or session["status"] != STATUS_PROBATION:
        # Cache was stale, fix it and return
        _probation_cache.discard(user.id)
        return

    # Strict rule: they must reply to a message sent by an admin
    if not update.message or not update.message.reply_to_message:
        return
        
    reply_to_user = update.message.reply_to_message.from_user
    if not reply_to_user or reply_to_user.id not in ADMIN_USER_IDS:
        return

    # User explicitly replied to an admin while on probation — they're safe!
    chat_id = session["chat_id"]
    database.update_session_status(user.id, STATUS_APPROVED)
    _probation_cache.discard(user.id)
    database.add_user_history(user.id, chat_id, "PROBATION_CLEARED", "User replied to admin in group within 7 days")
    logger.info("Probation cleared for user %s", user.id)

    safe_name = _safe_md(user.full_name)
    safe_username = f"(@{_safe_md(user.username)})" if user.username else ""
    await send_admin_notification(
        context,
        f"✅ *Probation Cleared*: {safe_name} {safe_username} (ID: `{user.id}`) successfully replied to an admin in the group.\n"
        f"Their 1-week probation has been lifted.",
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


async def _start_probation(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_text: str, log_msg: str) -> None:
    if not update.message or not update.message.text: return
    if not _is_admin(update): return
    target_user_id = _extract_target_id(update, context)
    if not target_user_id:
        await update.message.reply_text("Usage: Reply to a bot message with /probation_<lang> or type /probation_<lang> <user_id>")
        return
    try:
        sent_msg = await context.bot.send_message(chat_id=target_user_id, text=msg_text)
        database.add_bot_message_id(target_user_id, sent_msg.message_id)
        
        database.update_session_status(target_user_id, STATUS_PROBATION)
        _probation_cache.add(target_user_id)
        
        user_str = _format_user_string(target_user_id)
        await update.message.reply_text(f"✅ Sent {log_msg} probation warning to user {user_str} and started 1-week timer.")
        logger.info("Probation timer started for user %s", target_user_id)
        
        database.add_to_transcript(target_user_id, "admin", f"[1-Week Probation Started] {msg_text}")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not send probation warning to {target_user_id}: {e}")

async def on_admin_probation_en_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: /probation_en <user_id>"""
    msg = (
        "✅ You have been approved and added to the group!\n\n"
        "⚠️ *Important:* You must reply to the message where you were mentioned in the group by the admin within the next 7 days. "
        "If you do not reply within a week, you will be automatically kicked by the system."
    )
    await _start_probation(update, context, msg, "English")

async def on_admin_probation_ar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: /probation_ar <user_id>"""
    msg = (
        "✅ تمت الموافقة على انضمامك وتمت إضافتك إلى المجموعة!\n\n"
        "⚠️ *هام:* يجب عليك الرد على الرسالة التي تم الإشارة إليك فيها في المجموعة من قبل المسؤول خلال الـ 7 أيام القادمة. "
        "إذا لم تقم بالرد خلال أسبوع، فسيتم طردك تلقائيًا من قبل النظام."
    )
    await _start_probation(update, context, msg, "Arabic")


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
    target_user_id = _extract_target_id(update, context)
    if not target_user_id:
        await update.message.reply_text("Usage: Reply to a bot message with /reply <message> or type /reply <user_id> <message>")
        return
    
    # If the first arg is the ID, message text starts at args[1], else args[0]
    if args and args[0] == str(target_user_id):
        msg_text = " ".join(args[1:])
    else:
        msg_text = " ".join(args)
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
            
        # Log the admin message in the transcript
        database.add_to_transcript(target_user_id, "admin", msg_text)
        
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

    target_user_id = _extract_target_id(update, context)
    if not target_user_id:
        await update.message.reply_text("Usage: Reply to a bot message with /decline [reason] or type /decline <user_id> [reason]")
        return
        
    args = context.args or []
    if args and args[0] == str(target_user_id):
        reason = " ".join(args[1:]) if len(args) > 1 else "Your application did not meet the screening requirements."
    else:
        reason = " ".join(args) if args else "Your application did not meet the screening requirements."

    session = database.get_session(target_user_id)
    chat_id = session["chat_id"] if session else int(ADMIN_CHAT_ID or 0)

    # Per admin request, do NOT send a decline message to the user
    # We silently decline their join request instead.

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

async def on_admin_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: /clear <user_id> to delete bot DMs."""
    if not update.message or not update.message.text: return
    if not _is_admin(update): return
    target_user_id = _extract_target_id(update, context)
    if not target_user_id:
        await update.message.reply_text("Usage: Reply to a bot message with /clear or type /clear <user_id>")
        return
    await _delete_bot_messages(context, target_user_id)
    await update.message.reply_text(f"✅ Cleared bot messages for user {target_user_id}.")



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

    target_user_id = _extract_target_id(update, context)
    if not target_user_id:
        await update.message.reply_text("Usage: Reply to a bot message with /transcript or type /transcript <user_id>")
        return
    transcript_text = database.get_transcript_summary(target_user_id)
    
    if not transcript_text:
        await update.message.reply_text(f"No conversation transcript found for user ID {target_user_id}.")
        return
        
    user_str = _format_user_string(target_user_id)
    await update.message.reply_text(f"📄 Transcript for {user_str}:\n\n{transcript_text}")


async def on_admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a list of available admin commands."""
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
        await query.answer("Failed to delete message.", show_alert=True)

import traceback
import html

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer/admins."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    if context.error:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        
        # We only send the last 2000 chars of the traceback to not spam the chat
        error_msg = f"🚨 *CRITICAL BOT ERROR*\n\nThe bot just crashed while processing an update! Here is the error:\n\n<pre>{html.escape(tb_string[-2000:])}</pre>"
        
        target_id = DEVELOPER_CHAT_ID or 6260588359
        if target_id:
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=error_msg,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send error notification to {target_id}: {e}")

async def on_admin_crash_command(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intentionally crashes the bot to test the global error handler."""
    if not _is_admin(update):
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="💥 Triggering a fake crash now! You should receive the error report privately.")
    raise ValueError("THIS IS A TEST CRASH FOR THE DEVELOPER.")
