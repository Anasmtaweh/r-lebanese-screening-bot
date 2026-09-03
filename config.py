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

# Probation: 24 hours = 86400 seconds.
PROBATION_TIMEOUT_SECONDS = int(os.getenv("PROBATION_TIMEOUT_SECONDS", "86400"))

# Comma-separated list of Telegram user IDs allowed to run admin commands (/reply, /decline, /stats).
# Example: ADMIN_USER_IDS=123456789,987654321
ADMIN_USER_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]

# Exactly the screening paragraph requested by R/lebanese admins
SCREENING_QUESTIONS_EN = """Hello! R/lebanese admins here.

1. Are you Lebanese? If not, what country are you from?

2. Are you 18 or over?

3. How did you find out about our server?

4. Why are you interested in joining our server?

⚠️ Answering all questions is mandatory for your request to be reviewed.
Please note that not answering in 48 hours will result in request declining.
By joining, you agree to follow the group rules."""

SCREENING_QUESTIONS_AR = """مرحباً! إدارة مجتمع R/lebanese هنا.

1. هل أنت لبناني؟ إذا لا، من أي بلد أنت؟

2. هل عمرك 18 سنة أو أكثر؟

3. كيف عرفت عن السيرفر؟

4. لماذا تريد الانضمام إلى السيرفر؟

⚠️ الإجابة على جميع الأسئلة إلزامية لمراجعة طلبك.
ملاحظة: عدم الإجابة خلال 48 ساعة سيؤدي إلى رفض الطلب.
بانضمامك، أنت توافق على اتباع قوانين المجموعة."""

INCOMPLETE_PROMPT_EN = "Thank you for your response! However, it looks like you missed or didn't clearly answer the following:\n\n{missing_text}\n\nPlease reply with your complete answers so we can review your request!"

INCOMPLETE_PROMPT_AR = "شكراً على إجابتك! ولكن يبدو أنك لم تجب على جميع الأسئلة بوضوح. يرجى الإجابة على الأسئلة الناقصة:\n\n{missing_text}"

# Session Status Constants
STATUS_PENDING = "PENDING"
STATUS_PARTIAL = "PARTIAL"
STATUS_PASSED_TO_ADMINS = "PASSED_TO_ADMINS"
STATUS_AWAITING_USER_REPLY = "AWAITING_USER_REPLY"
STATUS_PROBATION = "PROBATION"
STATUS_APPROVED = "APPROVED"
STATUS_DECLINED = "DECLINED"
STATUS_DISMISSED = "DISMISSED"

# Healthchecks.io Ping URL for uptime monitoring
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "https://hc-ping.com/edc16fdc-dcb4-4daa-9548-48c8e76d0cd4")
