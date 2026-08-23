import database
import json

database.init_db()
database.add_or_reset_session(123, 456)
session = database.get_session(123)
print(f"Initial: {session['status']}, attempts: {session['attempt_count']}")

database.add_to_transcript(123, "user", "Hello")
database.increment_attempt_count(123)
session = database.get_session(123)
print(f"Transcript: {session['transcript_json']}, attempts: {session['attempt_count']}")

database.update_session_status(123, "PASSED_TO_ADMINS", "My answer")
session = database.get_session(123)
print(f"Status: {session['status']}, Answer: {session['answers_text']}")
