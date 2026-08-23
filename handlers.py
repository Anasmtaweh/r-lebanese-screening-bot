import logging
import re
from typing import Optional
from telegram import Chat, ChatMember, ChatMemberUpdated, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import database
from config import (
    ADMIN_CHAT_ID,
    ADMIN_USER_IDS,
    SCREENING_QUESTIONS,
    SCREENING_TIMEOUT_SECONDS,
    STATUS_APPROVED,
    STATUS_DECLINED,
    STATUS_DISMISSED,
    STATUS_PARTIAL,
    STATUS_PASSED_TO_ADMINS,
    STATUS_PENDING,
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


def _is_admin(update: Update) -> bool:
    """Returns True only if the message sender's Telegram user ID is in the ADMIN_USER_IDS whitelist."""
    user = update.effective_user
    if not user:
        return False
    return user.id in ADMIN_USER_IDS


async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Helper to send alerts or reports to the ADMIN_CHAT_ID if configured."""
    if not ADMIN_CHAT_ID:
        logger.info("[ADMIN_NOTIFY_SKIP] ADMIN_CHAT_ID not set. Message: %s", text)
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
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

    # 2. Initialize SQLite session with user metadata for future AI training
    user_metadata = {
        "username": user.username,
        "full_name": user.full_name,
        "is_premium": user.is_premium,
        "language_code": user.language_code
    }
    database.add_or_reset_session(user.id, chat.id, user_metadata)

    # 3. Send screening questions DM to user
    try:
        sent_msg = await context.bot.send_message(chat_id=user.id, text=SCREENING_QUESTIONS)
        database.add_bot_message_id(user.id, sent_msg.message_id)
        logger.info("Sent screening DM to user %s (message_id=%s)", user.id, sent_msg.message_id)
    except TelegramError as e:
        logger.error("Could not send screening DM to user %s: %s", user.id, e)
        await send_admin_notification(
            context,
            f"⚠️ Could not send screening DM to user {user.full_name} (ID: {user.id}): {e}\n"
            f"They might have blocked bots or privacy settings prevent DMs.",
        )
        return

    # 4. Schedule timeout job in JobQueue
    if context.job_queue:
        job_name = f"timeout_{user.id}_{chat.id}"
        # Remove any existing timeout jobs for this user
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()

        context.job_queue.run_once(
            on_timeout_job,
            when=SCREENING_TIMEOUT_SECONDS,
            data={"user_id": user.id, "chat_id": chat.id, "name": user.full_name},
            name=job_name,
        )
        logger.info("Scheduled timeout job %s in %s seconds", job_name, SCREENING_TIMEOUT_SECONDS)

    # 5. Notify Admins with clean, short notification
    await send_admin_notification(
        context,
        f"✉️ Screening DM sent successfully to User: {user.full_name} (@{user.username} | ID: {user.id})."
        f"{history_block}\n"
        f"48-hour rolling timer started.",
    )


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

    chat_id = session["chat_id"]
    database.add_to_transcript(user.id, "user", user_text)
    attempt_count = database.increment_attempt_count(user.id)

    # Rolling 48-hour timer reset
    if context.job_queue:
        job_name = f"timeout_{user.id}_{chat_id}"
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()
        context.job_queue.run_once(
            on_timeout_job,
            when=SCREENING_TIMEOUT_SECONDS,
            data={"user_id": user.id, "chat_id": chat_id, "name": user.full_name},
            name=job_name,
        )
        logger.info("Reset rolling 48-hour timeout job %s for user %s", job_name, user.id)

    res_type, feedback = evaluator.evaluate(user_text)
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
        admin_report = (
            f"📋 Satisfactory Screening Reply\n"
            f"From: {user.full_name} (@{user.username} | ID: {user.id})\n"
            f"Chat ID: {chat_id}"
            f"{history_block}\n\n"
            f"{transcript_text}\n\n"
            f"💡 Admins: Use `/reply {user.id} <msg>` to DM the user, or approve/decline manually in Telegram."
        )
        await send_admin_notification(context, admin_report)

    elif res_type == RESULT_INCOMPLETE:
        database.update_session_status(user.id, STATUS_PARTIAL, answers_text=user_text)
        try:
            follow_up_msg = await update.message.reply_text(feedback)
            database.add_bot_message_id(user.id, follow_up_msg.message_id)
            database.add_to_transcript(user.id, "bot", feedback)
        except TelegramError as e:
            logger.error("Could not send follow-up prompt to %s: %s", user.id, e)

        if attempt_count == 1:
            # Silently prompt user without alerting admins on 1st incomplete try
            logger.info("Attempt 1 incomplete for user %s. Sent silent follow-up prompt.", user.id)
        else:
            # On 2nd or later incomplete attempt, push full transcript to admins
            transcript_text = database.get_transcript_summary(user.id)
            await send_admin_notification(
                context,
                f"📋 Screening Report (2nd Attempt - Needs Admin Attention)\n"
                f"User: {user.full_name} (@{user.username} | ID: {user.id})\n"
                f"Chat ID: {chat_id}"
                f"{history_block}\n\n"
                f"{transcript_text}\n\n"
                f"💡 Admins: Use `/reply {user.id} <msg>` to DM the user, or `/decline {user.id} <reason>` to decline.",
            )

    elif res_type == RESULT_UNSATISFACTORY:
        # DO NOT auto-decline! Send for manual admin review
        database.update_session_status(user.id, STATUS_PASSED_TO_ADMINS, answers_text=user_text)
        database.add_to_transcript(user.id, "bot", "⚠️ Flagged by screening check")
        transcript_text = database.get_transcript_summary(user.id)

        await send_admin_notification(
            context,
            f"⚠️ Flagged Screening Reply (Under 18 / Review Needed)\n"
            f"User: {user.full_name} (@{user.username} | ID: {user.id})\n"
            f"Chat ID: {chat_id}"
            f"{history_block}\n\n"
            f"{transcript_text}\n\n"
            f"💡 Admins: Please review manually. Use `/reply {user.id} <msg>` or `/decline {user.id} <reason>`.",
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
        await send_admin_notification(
            context,
            f"🗑️ Automatically Declined: Junk Reply\n"
            f"User: {user.full_name} (@{user.username} | ID: {user.id})\n"
            f"Chat ID: {chat_id}"
            f"{history_block}\n\n"
            f"Their Reply: \"{user_text}\"",
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
    # Extract user ID from text like "ID: 123456789"
    match = re.search(r"ID:\s*(\d+)", replied_text)
    if not match:
        return

    target_user_id = int(match.group(1))
    admin_text = update.message.text

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💬 Message from R/lebanese Admin:\n\n{admin_text}",
        )
        await update.message.reply_text(
            f"✅ Your message has been relayed to user ID {target_user_id}."
        )
        logger.info("Admin relayed message to user %s", target_user_id)
    except TelegramError as e:
        logger.error("Could not relay message to user %s: %s", target_user_id, e)
        await update.message.reply_text(
            f"❌ Failed to send message to user ID {target_user_id}: {e}"
        )


async def on_timeout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered by JobQueue after SCREENING_TIMEOUT_SECONDS (48 hours by default).
    If the user has not satisfactorily completed screening, they are dismissed,
    join request declined, and DM messages deleted.
    """
    job = context.job
    if not job or not job.data:
        return

    user_id = job.data["user_id"]
    chat_id = job.data["chat_id"]
    user_name = job.data.get("name", "Unknown")

    session = database.get_active_session(user_id)
    if not session:
        return

    if session["status"] in (STATUS_PENDING, STATUS_PARTIAL):
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
            f"⏳ 48-Hour Timeout: User {user_name} (ID: {user_id}) did not reply in time.\n"
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
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💬 Message from R/lebanese Admin:\n\n{msg_text}",
        )
        await update.message.reply_text(f"✅ Sent DM to user {target_user_id}.")
        logger.info("Admin command /reply sent to %s", target_user_id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not send DM to {target_user_id}: {e}")


async def on_admin_decline_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /decline <user_id> [reason]
    Sends a decline DM to the applicant, declines their join request, and deletes screening DMs.
    """
    if not update.message or not update.message.text:
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
    await update.message.reply_text(f"🚫 User {target_user_id} declined & messages deleted.")


async def on_admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command: /stats
    Shows screening metrics for CV/monitoring (total requests, passed, declined junk, timeout).
    """
    if not update.message:
        return
    if not _is_admin(update):
        return

    stats = database.get_screening_stats()
    msg = (
        "📊 R/lebanese Screening Statistics:\n"
        f"• Total Join Requests: {stats['total_requests']}\n"
        f"• Passed Screening: {stats['passed']}\n"
        f"• Declined (Junk Reply): {stats['declined_junk']}\n"
        f"• Declined (48h Timeout): {stats['timeout']}\n"
        f"• Currently In Screening: {stats['active']}"
    )
    await update.message.reply_text(msg)

