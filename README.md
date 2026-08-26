# 🌲 R/lebanese Telegram Screening Bot

An autonomous, hardened Telegram screening and onboarding bot designed for the **R/lebanese** community. Built with [`python-telegram-bot` v21](https://github.com/python-telegram-bot/python-telegram-bot), PostgreSQL (Supabase), and Groq's **Llama 3 8B** AI classification.

---

## 🛠️ Tech Stack & Architecture
* **Language:** Python 3.10+
* **Framework:** `python-telegram-bot` (v21)
* **Database:** PostgreSQL (Hosted on Supabase)
* **Hosting:** Render Web Services (Webhook Mode)
* **AI Provider:** Groq (`llama3-8b-8192`)
* **Uptime Management:** Healthchecks.io (Outgoing Heartbeat) + cron-job.org (Incoming Wakeup Ping)

---

## ✨ How the Bot Works

### 1. The 4 Screening Questions
When a user requests to join the group, the bot sends them a private DM asking them to choose their language (English or Arabic), followed by exactly 4 questions:
1. Are you Lebanese?
2. Are you 18 or older? *(Must be 18+)*
3. How did you find out about our server?
4. Why are you interested in joining?

### 2. Smart 2-Attempt AI Screening Flow
* **Attempt #1 (Silent Prompt)**: The AI reads the user's reply. If it is incomplete (e.g., they only answered 2 out of 4 questions), the bot **silently** asks them for the specific missing questions without alerting admins.
* **Attempt #2 (Full Transcript to Admins)**: On their second attempt, the bot sends the complete 2-attempt conversation transcript to the Admin Channel so admins can take over manually.
* **Under 18 Protection**: If a user indicates they are under 18, they are flagged as `⚠️ Review Needed` in the Admin Channel. The bot never automatically accepts or declines users.

### 3. Rule-Based Emergency Fallback
If the Groq AI API goes down, times out, or fails for any reason, the bot will seamlessly and invisibly fall back to a **Hardcoded Rule-Based Evaluator**.
* The fallback evaluator is natively bilingual and scans the user's text for specific Arabic and English keywords (e.g., "نعم", "عشرين", "قوقل", "شات", "reddit", "18").
* It will gracefully handle length checks and exact keyword matching to ensure the screening process never stops working, even during a total AI outage.

### 4. 100% Manual Admin Control
The bot is designed to assist, not to make final decisions. Admins control everything directly from the Telegram group:
* `/reply <user_id> <message>` — Send a direct DM to an applicant. (Comes with an interactive `[Undo ↩️]` button if you make a typo!)
* `/decline <user_id> [reason]` — Silently decline a join request, send a decline reason DM, and delete all screening DMs.
* `/transcript <user_id>` — Read the exact private chat history between the bot and a specific user.
* `/stats` — View real-time screening metrics.
* `/list <category>` — View the last 20 users in categories like `passed`, `junk`, `timeout`, or `pending`.

### 5. Rolling 48-Hour Timeout
* Every time a message is sent between the bot and the user, a 48-hour timer resets.
* If an applicant goes unresponsive for 48 hours, their join request is automatically declined, DMs are deleted, and a timeout report is sent to admins.

---

## 🚀 Setup & Installation

### 1. Environment Variables (`.env`)
To run this bot, you need the following environment variables set (in Render or a local `.env` file):

```ini
# Core
BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
AI_API_KEY="gsk_YourGroqApiKeyHere"
DATABASE_URL="postgresql://postgres.xxx:password@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

# Admin Config
ADMIN_CHAT_ID="-1001234567890"
ADMIN_USER_IDS="123456789,987654321"

# Webhooks & Monitoring
RENDER_EXTERNAL_URL="https://your-bot-name.onrender.com"
HEALTHCHECK_URL="https://hc-ping.com/your-uuid"
```

### 2. Running Locally (Polling Mode)
If `RENDER_EXTERNAL_URL` is NOT set, the bot will automatically boot in standard Polling mode, which is ideal for local testing on your laptop.
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

### 3. Production Deployment (Webhook Mode)
When deployed to Render, `RENDER_EXTERNAL_URL` is set, which automatically triggers the bot to run a Tornado web server on port 10000. 
* Telegram will push updates directly to the Render URL.
* To prevent Render's free tier from putting the bot to sleep, configure a cronjob (like cron-job.org) to send a POST request with `{"update_id": 0}` (Content-Type: application/json) to your Render URL every 14 minutes.

---

## 📂 Project Structure
* `bot.py` — Main entry point, webhook/polling setup, and handler registration.
* `config.py` — Environment variables and hardcoded bilingual templates.
* `database.py` — PostgreSQL connection manager (with strict context managers to prevent connection exhaustion) and all DB queries.
* `evaluator.py` — The core logic containing the Groq AI classification and the emergency Rule-Based bilingual fallback.
* `handlers.py` — All Telegram event handlers (Join Requests, DM parsing, Admin Commands, and interactive callbacks).
