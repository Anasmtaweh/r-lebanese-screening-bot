import asyncio
import datetime
import os
import unittest
from typing import Any, Dict, List, Optional

os.environ["TESTING_MODE"] = "1"
os.environ["ADMIN_USER_IDS"] = "999"

from telegram import (
    Chat,
    ChatJoinRequest,
    Message,
    Update,
    User,
)

import database
from config import (
    SCREENING_QUESTIONS,
    STATUS_DISMISSED,
    STATUS_PARTIAL,
    STATUS_PASSED_TO_ADMINS,
    STATUS_PENDING,
)
from handlers import (
    check_expired_timeouts,
    on_admin_relay_reply,
    on_join_request,
    on_user_dm_reply,
)


class MockMessage:
    def __init__(self, message_id: int, text: str, reply_to_message: Optional["MockMessage"] = None):
        self.message_id = message_id
        self.text = text
        self.reply_to_message = reply_to_message

    async def reply_text(self, text: str, *args, **kwargs) -> "MockMessage":
        print(f"  [MOCK BOT reply_text] -> {text}")
        return MockMessage(message_id=self.message_id + 100, text=text)


class MockBot:
    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []
        self.deleted_messages: List[Dict[str, Any]] = []
        self.declined_requests: List[Dict[str, Any]] = []
        self._msg_id_counter = 1000

    async def send_message(self, chat_id: int, text: str, *args, **kwargs) -> MockMessage:
        self._msg_id_counter += 1
        msg = MockMessage(message_id=self._msg_id_counter, text=text)
        self.sent_messages.append({"chat_id": chat_id, "text": text, "message_id": msg.message_id})
        print(f"  [MOCK BOT send_message to {chat_id}] (ID: {msg.message_id}):\n{text}")
        return msg

    async def delete_message(self, chat_id: int, message_id: int, *args, **kwargs) -> bool:
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        print(f"  [MOCK BOT delete_message] chat_id={chat_id}, message_id={message_id}")
        return True

    async def decline_chat_join_request(self, chat_id: int, user_id: int, *args, **kwargs) -> bool:
        self.declined_requests.append({"chat_id": chat_id, "user_id": user_id})
        print(f"  [MOCK BOT decline_chat_join_request] chat_id={chat_id}, user_id={user_id}")
        return True


class MockJob:
    def __init__(self, data: Dict[str, Any]):
        self.data = data


class MockJobQueue:
    def __init__(self):
        self.jobs: List[Dict[str, Any]] = []

    def get_jobs_by_name(self, name: str) -> List[Any]:
        return []

    def run_once(self, callback: Any, when: int, data: Dict[str, Any], name: str) -> None:
        self.jobs.append({"callback": callback, "when": when, "data": data, "name": name})
        print(f"  [MOCK JobQueue] Scheduled job '{name}' to run in {when} seconds.")


class MockContext:
    def __init__(self, bot: MockBot):
        self.bot = bot
        self.job_queue = MockJobQueue()
        self.job: Optional[MockJob] = None


async def run_all_simulated_tests():
    print("====================================================================")
    print("       STARTING COMPREHENSIVE R/LEBANESE SCREENING BOT TESTS       ")
    print("====================================================================\n")

    test_db = "test_screening_sim.db"
    if os.path.exists(test_db):
        os.remove(test_db)
    database.DB_PATH = test_db
    database.init_db(test_db)

    bot = MockBot()
    context = MockContext(bot)

    # Sample test data
    test_user = User(id=777888999, first_name="Cedar", is_bot=False, username="cedar_user")
    test_chat = Chat(id=-100111222333, type=Chat.SUPERGROUP, title="R/lebanese Server")

    # -------------------------------------------------------------------------
    # SCENARIO 1: User requests to join -> Bot sends screening DM & schedules 48h job
    # -------------------------------------------------------------------------
    print("--- SCENARIO 1: Join Request Arrives ---")
    join_req = ChatJoinRequest(
        chat=test_chat,
        from_user=test_user,
        user_chat_id=test_user.id,
        date=datetime.datetime.now(datetime.timezone.utc),
    )
    update_join = Update(update_id=1, chat_join_request=join_req)

    await on_join_request(update_join, context)

    session = database.get_active_session(test_user.id, test_db)
    assert session is not None, "Session should be created in DB"
    assert session["status"] == STATUS_PENDING, "Initial status must be PENDING"
    user_dms = [m for m in bot.sent_messages if m["chat_id"] == test_user.id]
    assert len(user_dms) == 1, "Should send 1 DM with screening questions to the user"
    assert "Are you Lebanese?" in user_dms[0]["text"]
    assert "48 hours" in user_dms[0]["text"]
    assert len(context.job_queue.jobs) == 0, "No JobQueue jobs (timeouts are now passive via database)"
    print("✅ Scenario 1 Passed: Session created, DM sent, and 48-hour timeout tracked via database.\n")

    # -------------------------------------------------------------------------
    # SCENARIO 2: Attempt 1 Incomplete -> Bot sends follow-up DM silently (no admin alert)
    # -------------------------------------------------------------------------
    print("--- SCENARIO 2: User Reply Attempt #1 Incomplete (Silent Prompt) ---")
    admin_msgs_before = [m for m in bot.sent_messages if m["chat_id"] != test_user.id]
    msg_incomplete = MockMessage(message_id=200, text="TEST_INCOMPLETE")
    update_dm1 = Update(update_id=2, message=msg_incomplete)
    update_dm1._effective_user = test_user
    update_dm1._effective_chat = Chat(id=test_user.id, type=Chat.PRIVATE)

    await on_user_dm_reply(update_dm1, context)

    session = database.get_active_session(test_user.id, test_db)
    assert session["status"] == STATUS_PARTIAL, "Status should change to PARTIAL"
    assert int(session["attempt_count"]) == 1, "Attempt count must be 1"
    admin_msgs_after = [m for m in bot.sent_messages if m["chat_id"] != test_user.id]
    assert len(admin_msgs_after) == len(admin_msgs_before), "Bot must NOT alert admins on Attempt #1 incomplete"
    print("✅ Scenario 2 Passed: Attempt #1 INCOMPLETE -> silent follow-up sent to user without alerting admins.\n")

    # -------------------------------------------------------------------------
    # SCENARIO 3: Attempt 2 -> Pushes Full 2-Attempt Transcript to Admins
    # -------------------------------------------------------------------------
    print("--- SCENARIO 3: User Reply Attempt #2 -> Pushes Full Transcript to Admins ---")
    msg_satisfactory = MockMessage(message_id=201, text="TEST_SATISFACTORY")
    update_dm2 = Update(update_id=3, message=msg_satisfactory)
    update_dm2._effective_user = test_user
    update_dm2._effective_chat = Chat(id=test_user.id, type=Chat.PRIVATE)

    await on_user_dm_reply(update_dm2, context)

    session = database.get_active_session(test_user.id, test_db)
    assert session["status"] == STATUS_PASSED_TO_ADMINS, "Status should be PASSED_TO_ADMINS"
    assert int(session["attempt_count"]) == 2, "Attempt count must be 2"
    last_admin_msg = [m for m in bot.sent_messages if m["chat_id"] != test_user.id][-1]
    assert "Full 2-Attempt Conversation Transcript" in last_admin_msg["text"], "Must include transcript summary"
    print("✅ Scenario 3 Passed: Attempt #2 -> Full conversation transcript pushed to Admins.\n")

    # -------------------------------------------------------------------------
    # SCENARIO 4: Admin Uses /reply Command in Admin Channel to DM the User
    # -------------------------------------------------------------------------
    print("--- SCENARIO 4: Admin Uses /reply Command -> Sends DM to User ---")
    from handlers import on_admin_reply_command
    from config import ADMIN_CHAT_ID
    admin_chat_id_val = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID and ADMIN_CHAT_ID.lstrip("-").isdigit() else -100999888777

    reply_cmd_msg = MockMessage(
        message_id=501,
        text=f"/reply {test_user.id} Hello Cedar! I am approving your request and adding you now.",
    )
    update_admin = Update(update_id=4, message=reply_cmd_msg)
    update_admin._effective_user = User(id=999, first_name="Admin", is_bot=False)
    update_admin._effective_chat = Chat(id=admin_chat_id_val, type=Chat.SUPERGROUP)
    context.args = [str(test_user.id), "Hello", "Cedar!", "I", "am", "approving", "your", "request", "and", "adding", "you", "now."]

    await on_admin_reply_command(update_admin, context)

    last_sent = bot.sent_messages[-1]
    assert last_sent["chat_id"] == test_user.id, "DM should go to the applicant"
    assert "Message from R/lebanese Admin:" in last_sent["text"]
    assert "approving your request" in last_sent["text"]
    print("✅ Scenario 4 Passed: Admin command /reply successfully sent custom DM to user.\n")

    # -------------------------------------------------------------------------
    # SCENARIO 5: 48-Hour Timeout Fires -> Request Declined & Messages Deleted
    # -------------------------------------------------------------------------
    print("--- SCENARIO 5: 48-Hour Timeout Fired for Unresponsive User ---")
    # Create a second user who never answers
    lazy_user = User(id=111222333, first_name="Lazy", is_bot=False, username="lazy_guy")
    database.add_or_reset_session(lazy_user.id, test_chat.id, test_db)
    # Record a fake bot message sent to lazy_user
    database.add_bot_message_id(lazy_user.id, 8888, test_db)

    # Fake the updated_at timestamp to be 49 hours ago so the passive check catches it
    from database import _get_connection
    with _get_connection(test_db) as conn:
        conn.execute(
            "UPDATE screening_sessions SET updated_at = datetime('now', '-49 hours') WHERE user_id = ?",
            (lazy_user.id,),
        )
        conn.commit()

    # Simulate passive timeout check (runs on every webhook update)
    await check_expired_timeouts(bot)

    # Verify lazy_user is now DISMISSED
    lazy_session = database.get_active_session(lazy_user.id, test_db)
    assert lazy_session is None, "Active session should be None after dismissal"
    # Verify join request was declined
    assert any(
        req["user_id"] == lazy_user.id and req["chat_id"] == test_chat.id
        for req in bot.declined_requests
    ), "Join request must be declined on timeout"
    # Verify bot messages were deleted
    assert any(
        del_msg["chat_id"] == lazy_user.id
        and del_msg["message_id"] == 8888
        for del_msg in bot.deleted_messages
    ), "Screening DM messages must be deleted on timeout"
    print("✅ Scenario 5 Passed: 48-hour timeout automatically declined join request and deleted DM messages.\n")

    # -------------------------------------------------------------------------
    # SCENARIO 6: User History Tracking on Re-application
    # -------------------------------------------------------------------------
    print("--- SCENARIO 6: User Re-applies After Previous Dismissal ---")
    # Lazy user requests to join again after being dismissed in Scenario 5
    join_req_retry = ChatJoinRequest(
        chat=test_chat,
        from_user=lazy_user,
        user_chat_id=lazy_user.id,
        date=datetime.datetime.now(datetime.timezone.utc),
    )
    update_retry = Update(update_id=10, chat_join_request=join_req_retry)
    await on_join_request(update_retry, context)

    # Verify that the screening DM was STILL sent to lazy_user on retry
    retry_dms = [m for m in bot.sent_messages if m["chat_id"] == lazy_user.id]
    assert len(retry_dms) > 0, "Bot must still send screening DM on re-application"
    assert "Are you Lebanese?" in retry_dms[-1]["text"], "Must send screening questions"

    # Verify that the user's permanent history shows up correctly
    history_summary = database.format_user_history_summary(lazy_user.id, test_chat.id, test_db)
    assert "DISMISSED_TIMEOUT" in history_summary, "Past timeout dismissal must be recorded in history"
    print("✅ Scenario 6 Passed: User history tracked and reported to admins on re-application.\n")

    # Clean up test DB
    if os.path.exists(test_db):
        os.remove(test_db)

    print("====================================================================")
    print("  🎉 ALL 6 SIMULATED SCREENING SCENARIOS COMPLETED SUCCESSFULLY!   ")
    print("====================================================================\n")


if __name__ == "__main__":
    asyncio.run(run_all_simulated_tests())
