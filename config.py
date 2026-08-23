import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# ADMIN_CHAT_ID is where user screening responses will be sent.
# Admins can reply to messages in this chat to talk to the user.
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# Default 48 hours = 172800 seconds.
# In .env, you can set SCREENING_TIMEOUT_SECONDS=10 for fast local testing!
SCREENING_TIMEOUT_SECONDS = int(os.getenv("SCREENING_TIMEOUT_SECONDS", "172800"))

# Comma-separated list of Telegram user IDs allowed to run admin commands (/reply, /decline, /stats).
# Example: ADMIN_USER_IDS=123456789,987654321
ADMIN_USER_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]

# Exactly the screening paragraph requested by R/lebanese admins
SCREENING_QUESTIONS = """Hello
R/lebanese admins here

Are you Lebanese?

Are you 18 or over?

How did you find out about our server?

Why are you interested in joining our server?

Please note that not answering in 48 hours will result in request declining."""

# Session Status Constants
STATUS_PENDING = "PENDING"
STATUS_PARTIAL = "PARTIAL"
STATUS_PASSED_TO_ADMINS = "PASSED_TO_ADMINS"
STATUS_APPROVED = "APPROVED"
STATUS_DECLINED = "DECLINED"
STATUS_DISMISSED = "DISMISSED"
