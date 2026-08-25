import json
import sqlite3
from typing import Any, Dict, List, Optional
from config import STATUS_PENDING, STATUS_PARTIAL, STATUS_DISMISSED

DB_PATH = "screening.db"


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screening_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            bot_message_ids TEXT NOT NULL DEFAULT '[]',
            answers_text TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            transcript_json TEXT NOT NULL DEFAULT '[]',
            user_metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, chat_id)
        )
        """
    )
    for col_sql in [
        "ALTER TABLE screening_sessions ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE screening_sessions ADD COLUMN transcript_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE screening_sessions ADD COLUMN user_metadata_json TEXT NOT NULL DEFAULT '{}'",
    ]:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def add_user_history(
    user_id: int, chat_id: int, event_type: str, details: str = "", db_path: Optional[str] = None
) -> None:
    """Records an event in the user's permanent history (e.g. DISMISSED_TIMEOUT, APPROVED_JOINED, LEFT_GROUP)."""
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO user_history (user_id, chat_id, event_type, details, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, chat_id, event_type, details),
        )
        conn.commit()


def get_user_history(
    user_id: int, chat_id: int, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Returns all past history events for a user in chronological order."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM user_history
            WHERE user_id = ? AND chat_id = ?
            ORDER BY created_at DESC
            """,
            (user_id, chat_id),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def format_user_history_summary(
    user_id: int, chat_id: int, db_path: Optional[str] = None
) -> str:
    """Returns a formatted human-readable summary of the user's past history, or empty string if no history."""
    history = get_user_history(user_id, chat_id, db_path)
    if not history:
        return ""

    lines = ["📜 Previous History for this User:"]
    for item in history[:5]:  # show up to 5 most recent events
        date_str = str(item["created_at"])[:16]  # YYYY-MM-DD HH:MM
        event = item["event_type"]
        details = f" ({item['details']})" if item["details"] else ""
        lines.append(f"  • [{date_str}] {event}{details}")

    return "\n".join(lines)



def init_db(db_path: Optional[str] = None) -> None:
    """Initializes the SQLite database table for screening sessions."""
    with _get_connection(db_path):
        pass


def add_or_reset_session(user_id: int, chat_id: int, user_metadata: Optional[Dict[str, Any]] = None, db_path: Optional[str] = None) -> None:
    """Creates a new screening session or resets an existing one to PENDING."""
    meta_json = json.dumps(user_metadata) if user_metadata else '{}'
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO screening_sessions (user_id, chat_id, status, bot_message_ids, answers_text, attempt_count, transcript_json, user_metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, '[]', '', 0, '[]', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                status = excluded.status,
                bot_message_ids = '[]',
                answers_text = '',
                attempt_count = 0,
                transcript_json = '[]',
                user_metadata_json = excluded.user_metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, chat_id, STATUS_PENDING, meta_json),
        )
        conn.commit()


def get_session(user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns the latest session for the user regardless of status."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM screening_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_active_session(user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns an active session for the user if they are not DISMISSED."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM screening_sessions
            WHERE user_id = ? AND status != ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (user_id, STATUS_DISMISSED),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_expired_sessions(hours: int = 48, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns all pending/partial sessions that have not been updated in the last N hours."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM screening_sessions
            WHERE status IN (?, ?)
            AND updated_at <= datetime('now', ?)
            """,
            (STATUS_PENDING, STATUS_PARTIAL, f"-{hours} hours"),
        )
        return [dict(row) for row in cursor.fetchall()]


def update_session_status(
    user_id: int, status: str, answers_text: Optional[str] = None, db_path: Optional[str] = None
) -> None:
    """Updates the session status and optionally updates the recorded answers text."""
    with _get_connection(db_path) as conn:
        if answers_text is not None:
            conn.execute(
                """
                UPDATE screening_sessions
                SET status = ?, answers_text = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (status, answers_text, user_id),
            )
        else:
            conn.execute(
                """
                UPDATE screening_sessions
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (status, user_id),
            )
        conn.commit()


def add_bot_message_id(user_id: int, message_id: int, db_path: Optional[str] = None) -> None:
    """Records a message ID sent by the bot to the user so it can be deleted on timeout/dismissal."""
    session = get_session(user_id, db_path)
    if not session:
        return
    msg_ids: List[int] = json.loads(session["bot_message_ids"] or "[]")
    if message_id not in msg_ids:
        msg_ids.append(message_id)
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE screening_sessions
            SET bot_message_ids = ?
            WHERE user_id = ?
            """,
            (json.dumps(msg_ids), user_id),
        )
        conn.commit()


def get_bot_message_ids(user_id: int, db_path: Optional[str] = None) -> List[int]:
    """Returns all bot message IDs recorded for this user's screening session."""
    session = get_session(user_id, db_path)
    if not session:
        return []
    return json.loads(session["bot_message_ids"] or "[]")


def increment_attempt_count(user_id: int, db_path: Optional[str] = None) -> int:
    """Increments the attempt counter for this user's screening session and returns the new count."""
    session = get_session(user_id, db_path)
    if not session:
        return 1
    new_count = int(session["attempt_count"] or 0) + 1
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE screening_sessions
            SET attempt_count = ?
            WHERE user_id = ?
            """,
            (new_count, user_id),
        )
        conn.commit()
    return new_count


def add_to_transcript(user_id: int, role: str, text: str, db_path: Optional[str] = None) -> None:
    """Appends a message to the user's screening transcript."""
    session = get_session(user_id, db_path)
    if not session:
        return
    transcript: List[Dict[str, str]] = json.loads(session["transcript_json"] or "[]")
    transcript.append({"role": role, "text": text})
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE screening_sessions
            SET transcript_json = ?
            WHERE user_id = ?
            """,
            (json.dumps(transcript), user_id),
        )
        conn.commit()


def get_transcript_summary(user_id: int, db_path: Optional[str] = None) -> str:
    """Returns a formatted human-readable summary of the entire 2-attempt conversation transcript."""
    session = get_session(user_id, db_path)
    if not session:
        return ""
    transcript: List[Dict[str, str]] = json.loads(session["transcript_json"] or "[]")
    if not transcript:
        return "*(User has not sent any replies yet)*"
    lines = ["💬 Full 2-Attempt Conversation Transcript:"]
    for i, item in enumerate(transcript, 1):
        role_label = "👤 User Reply" if item["role"] == "user" else "🤖 Bot Follow-up"
        lines.append(f"  [{role_label} #{i}]: \"{item['text']}\"")
    return "\n".join(lines)


def get_all_user_replies_combined(user_id: int, db_path: Optional[str] = None) -> str:
    """Returns all user replies from the transcript combined into a single text block for evaluation."""
    session = get_session(user_id, db_path)
    if not session:
        return ""
    transcript: List[Dict[str, str]] = json.loads(session["transcript_json"] or "[]")
    user_replies = [item["text"] for item in transcript if item["role"] == "user"]
    return "\n".join(user_replies)


def get_screening_stats(db_path: Optional[str] = None) -> Dict[str, int]:
    """Returns overall screening statistics for CV/metrics monitoring."""
    with _get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM screening_sessions")
        total_requests = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM user_history WHERE event_type = 'PASSED_SCREENING'")
        passed = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM user_history WHERE event_type = 'DECLINED_JUNK'")
        declined_junk = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM user_history WHERE event_type = 'DISMISSED_TIMEOUT'")
        timeout = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM screening_sessions WHERE status IN (?, ?)", ("PENDING", "PARTIAL"))
        active = cursor.fetchone()[0] or 0

        return {
            "total_requests": total_requests,
            "passed": passed,
            "declined_junk": declined_junk,
            "timeout": timeout,
            "active": active,
        }

def get_recent_users_by_event(event_type: str, limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns a list of recent users who triggered a specific history event (e.g. PASSED_SCREENING)."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT h.user_id, h.created_at, s.user_metadata_json
            FROM user_history h
            LEFT JOIN screening_sessions s ON h.user_id = s.user_id
            WHERE h.event_type = ?
            ORDER BY h.created_at DESC
            LIMIT ?
            """,
            (event_type, limit)
        )
        rows = []
        for row in cursor.fetchall():
            r = dict(row)
            try:
                r["metadata"] = json.loads(r["user_metadata_json"]) if r["user_metadata_json"] else {}
            except Exception:
                r["metadata"] = {}
            rows.append(r)
        return rows


def get_pending_users(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns a list of users currently in PENDING or PARTIAL status."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT user_id, updated_at as created_at, user_metadata_json
            FROM screening_sessions
            WHERE status IN ('PENDING', 'PARTIAL')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = []
        for row in cursor.fetchall():
            r = dict(row)
            try:
                r["metadata"] = json.loads(r["user_metadata_json"]) if r["user_metadata_json"] else {}
            except Exception:
                r["metadata"] = {}
            rows.append(r)
        return rows

