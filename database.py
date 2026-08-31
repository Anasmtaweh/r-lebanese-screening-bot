import json
import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from typing import Any, Dict, List, Optional
from contextlib import contextmanager
from config import STATUS_PENDING, STATUS_DISMISSED, STATUS_PARTIAL

_db_pool = None

def get_db_url() -> str:
    from dotenv import load_dotenv
    load_dotenv()
    return os.environ.get("DATABASE_URL", "")

def _init_pool():
    global _db_pool
    if _db_pool is None:
        url = get_db_url()
        if url:
            _db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, url, cursor_factory=RealDictCursor)

@contextmanager
def _get_connection():
    global _db_pool
    if _db_pool is None:
        _init_pool()
    
    if _db_pool:
        conn = _db_pool.getconn()
        try:
            # Ping to check if connection is alive (handles idle drop by Supabase)
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # Connection is dead, throw it away and create a fresh one
            _db_pool.putconn(conn, close=True)
            conn = psycopg2.connect(get_db_url(), cursor_factory=RealDictCursor)

        try:
            yield conn
        finally:
            _db_pool.putconn(conn)
    else:
        # Fallback if DATABASE_URL is missing
        conn = psycopg2.connect(get_db_url(), cursor_factory=RealDictCursor)
        try:
            yield conn
        finally:
            conn.close()

def init_db() -> None:
    """Initializes the PostgreSQL database tables for screening sessions."""
    url = get_db_url()
    if not url:
        print("DATABASE_URL is missing. Cannot initialize DB.")
        return

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        conn.commit()


def add_user_history(
    user_id: int, chat_id: int, event_type: str, details: str = ""
) -> None:
    """Records an event in the user's permanent history (e.g. DISMISSED_TIMEOUT, APPROVED_JOINED, LEFT_GROUP)."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_history (user_id, chat_id, event_type, details, created_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (user_id, chat_id, event_type, details),
            )
        conn.commit()


def get_user_history(
    user_id: int, chat_id: int
) -> List[Dict[str, Any]]:
    """Returns all past history events for a user in chronological order."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM user_history
                WHERE user_id = %s AND chat_id = %s
                ORDER BY created_at DESC
                """,
                (user_id, chat_id),
            )
            return [dict(r) for r in cur.fetchall()]


def _safe_md_db(text: str) -> str:
    if not text: return ""
    return str(text).replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')

def format_user_history_summary(
    user_id: int, chat_id: int
) -> str:
    """Returns a formatted human-readable summary of the user's past history."""
    history = get_user_history(user_id, chat_id)
    if not history:
        return ""

    lines = ["📜 Previous History for this User:"]
    for item in history[:5]:  # show up to 5 most recent events
        date_str = str(item["created_at"])[:16]  # YYYY-MM-DD HH:MM
        event = _safe_md_db(item["event_type"])
        details = f" ({_safe_md_db(item['details'])})" if item["details"] else ""
        lines.append(f"  • [{date_str}] {event}{details}")

    return "\n".join(lines)


def add_or_reset_session(user_id: int, chat_id: int, user_metadata: Optional[Dict[str, Any]] = None) -> None:
    """Creates a new screening session or resets an existing one to PENDING."""
    meta_json = json.dumps(user_metadata) if user_metadata else '{}'
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO screening_sessions (user_id, chat_id, status, bot_message_ids, answers_text, attempt_count, transcript_json, user_metadata_json, created_at, updated_at)
                VALUES (%s, %s, %s, '[]', '', 0, '[]', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    bot_message_ids = '[]',
                    answers_text = '',
                    attempt_count = 0,
                    transcript_json = '[]',
                    user_metadata_json = EXCLUDED.user_metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, chat_id, STATUS_PENDING, meta_json),
            )
        conn.commit()


def get_session(user_id: int) -> Optional[Dict[str, Any]]:
    """Returns the latest session for the user regardless of status."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM screening_sessions
                WHERE user_id = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
    return None


def update_session_language(user_id: int, language_code: str) -> None:
    """Updates the selected language code in the user's metadata JSON."""
    session = get_session(user_id)
    if not session:
        return
    meta = json.loads(session["user_metadata_json"] or "{}")
    meta["language_code"] = language_code
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE screening_sessions
                SET user_metadata_json = %s
                WHERE user_id = %s
                """,
                (json.dumps(meta), user_id),
            )
        conn.commit()


def get_active_session(user_id: int) -> Optional[Dict[str, Any]]:
    """Returns an active session for the user if they are not DISMISSED."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM screening_sessions
                WHERE user_id = %s AND status != %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id, STATUS_DISMISSED),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
    return None


def update_session_status(
    user_id: int, status: str, answers_text: Optional[str] = None
) -> None:
    """Updates the session status and optionally updates the recorded answers text."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            if answers_text is not None:
                cur.execute(
                    """
                    UPDATE screening_sessions
                    SET status = %s, answers_text = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                    """,
                    (status, answers_text, user_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE screening_sessions
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                    """,
                    (status, user_id),
                )
        conn.commit()


def add_bot_message_id(user_id: int, message_id: int) -> None:
    """Records a message ID sent by the bot to the user so it can be deleted on timeout/dismissal."""
    session = get_session(user_id)
    if not session:
        return
    msg_ids: List[int] = json.loads(session["bot_message_ids"] or "[]")
    if message_id not in msg_ids:
        msg_ids.append(message_id)
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE screening_sessions
                SET bot_message_ids = %s
                WHERE user_id = %s
                """,
                (json.dumps(msg_ids), user_id),
            )
        conn.commit()


def get_bot_message_ids(user_id: int) -> List[int]:
    """Returns all bot message IDs recorded for this user's screening session."""
    session = get_session(user_id)
    if not session:
        return []
    return json.loads(session["bot_message_ids"] or "[]")


def get_expired_sessions(timeout_seconds: int) -> List[Dict[str, Any]]:
    """Returns all PENDING or PARTIAL sessions where updated_at is older than timeout_seconds."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM screening_sessions
                WHERE status IN (%s, %s)
                  AND updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                """,
                (STATUS_PENDING, STATUS_PARTIAL, timeout_seconds),
            )
            return cur.fetchall()


def increment_attempt_count(user_id: int) -> int:
    """Increments the attempt counter for this user's screening session and returns the new count."""
    session = get_session(user_id)
    if not session:
        return 1
    new_count = int(session["attempt_count"] or 0) + 1
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE screening_sessions
                SET attempt_count = %s
                WHERE user_id = %s
                """,
                (new_count, user_id),
            )
        conn.commit()
    return new_count


def add_to_transcript(user_id: int, role: str, text: str) -> None:
    """Appends a message to the user's screening transcript."""
    session = get_session(user_id)
    if not session:
        return
    transcript: List[Dict[str, str]] = json.loads(session["transcript_json"] or "[]")
    transcript.append({"role": role, "text": text})
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE screening_sessions
                SET transcript_json = %s
                WHERE user_id = %s
                """,
                (json.dumps(transcript), user_id),
            )
        conn.commit()


def get_transcript_summary(user_id: int) -> str:
    """Returns a formatted human-readable summary of the entire 2-attempt conversation transcript."""
    session = get_session(user_id)
    if not session:
        return ""
    transcript: List[Dict[str, str]] = json.loads(session["transcript_json"] or "[]")
    if not transcript:
        return "*(User has not sent any replies yet)*"
    lines = ["💬 _Full Conversation Transcript:_"]
    reply_num = 0
    for i, item in enumerate(transcript, 1):
        if item["role"] == "user":
            reply_num += 1
            safe_text = item['text'].replace('*', '').replace('_', '').replace('`', '')
            lines.append(f"👤 *User Reply #{reply_num}:*\n「{safe_text}」")
        else:
            bullet_lines = [line.strip() for line in item['text'].split('\n') if line.strip().startswith('•')]
            if bullet_lines:
                bullets = '\n'.join(f"  {line}" for line in bullet_lines)
                lines.append(f"🤖 _Bot asked for:_\n{bullets}")
            else:
                safe_bot_text = item['text'].replace('*', '').replace('_', '').replace('`', '')
                lines.append(f"🤖 _Bot:_\n「{safe_bot_text}」")
    return "\n\n".join(lines)


def get_all_user_replies_combined(user_id: int) -> str:
    """Returns all user replies from the transcript combined into a single text block for evaluation."""
    session = get_session(user_id)
    if not session:
        return ""
    transcript: List[Dict[str, str]] = json.loads(session["transcript_json"] or "[]")
    user_replies = [item["text"] for item in transcript if item["role"] == "user"]
    return "\n".join(user_replies)


def get_screening_stats() -> Dict[str, int]:
    """Returns overall screening statistics for CV/metrics monitoring."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT user_id) as c FROM screening_sessions")
            total_requests = cur.fetchone()["c"] or 0

            cur.execute("SELECT COUNT(*) as c FROM user_history WHERE event_type = 'PASSED_SCREENING'")
            passed = cur.fetchone()["c"] or 0

            cur.execute("SELECT COUNT(*) as c FROM user_history WHERE event_type = 'DECLINED_JUNK'")
            declined_junk = cur.fetchone()["c"] or 0

            cur.execute("SELECT COUNT(*) as c FROM user_history WHERE event_type = 'DISMISSED_TIMEOUT'")
            timeout = cur.fetchone()["c"] or 0

            cur.execute("SELECT COUNT(*) as c FROM user_history WHERE event_type = 'APPROVED_JOINED'")
            accepted = cur.fetchone()["c"] or 0

            cur.execute("SELECT COUNT(*) as c FROM screening_sessions WHERE status IN (%s, %s)", ("PENDING", "PARTIAL"))
            active = cur.fetchone()["c"] or 0

            return {
                "total_requests": total_requests,
                "passed": passed,
                "declined_junk": declined_junk,
                "timeout": timeout,
                "accepted": accepted,
                "active": active,
            }

def get_recent_users_by_event(event_type: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns a list of recent users who triggered a specific history event (e.g. PASSED_SCREENING)."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT h.user_id, h.created_at, s.user_metadata_json
                FROM user_history h
                LEFT JOIN screening_sessions s ON h.user_id = s.user_id
                WHERE h.event_type = %s
                ORDER BY h.created_at DESC
                LIMIT %s
                """,
                (event_type, limit)
            )
            rows = []
            for r in cur.fetchall():
                try:
                    r["metadata"] = json.loads(r["user_metadata_json"]) if r.get("user_metadata_json") else {}
                except Exception:
                    r["metadata"] = {}
                rows.append(dict(r))
            return rows


def get_pending_users(limit: int = 20) -> List[Dict[str, Any]]:
    """Returns the most recent users whose sessions are PENDING or PARTIAL (Currently In Screening)."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, updated_at as created_at, user_metadata_json
                FROM screening_sessions
                WHERE status IN ('PENDING', 'PARTIAL')
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,)
            )
            rows = []
            for r in cur.fetchall():
                try:
                    r["metadata"] = json.loads(r["user_metadata_json"]) if r.get("user_metadata_json") else {}
                except Exception:
                    r["metadata"] = {}
                rows.append(dict(r))
            return rows
