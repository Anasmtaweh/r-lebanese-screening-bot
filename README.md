# 🌲 R/lebanese Telegram Screening & Onboarding Bot

An autonomous, hardened Telegram screening and onboarding bot designed for the **R/lebanese** community. Built with [`python-telegram-bot` v21](https://github.com/python-telegram-bot/python-telegram-bot), SQLite, and **Groq Llama-3.1-8B Instant** AI classification.

---

## ✨ Key Features

* **🧠 Smart 2-Attempt Screening Flow**:
  * **Attempt #1 (Silent Prompt)**: When a user requests to join, the bot sends them a private DM with 4 screening questions. If their first reply is incomplete, the bot **silently** asks for the specific missing questions without alerting or spamming admins.
  * **Attempt #2 (Full Transcript to Admins)**: On their second attempt, the bot sends the complete 2-attempt conversation transcript to the Admin Channel so admins can review the interaction.
* **🛡️ Hardened AI Clamps & Anti-Prompt Injection**:
  * The LLM is clamped to output **only** classification tokens (`SATISFACTORY`, `INCOMPLETE | <numbers>`, or `UNSATISFACTORY`).
  * Users **never** see AI-generated text. The bot only sends hardcoded, pre-written question templates, preventing prompt injection, AI jailbreaks, or casual chatting.
  * Supports English, Arabic, and Lebanese Franco-Arabic dialect (e.g., `"eh lebanese akid, 25 sene"`).
* **👨‍✈️ 100% Manual Admin Control (No Auto-Decisions)**:
  * The bot **never** automatically accepts or declines an applicant (unless they time out or send junk).
  * If a user indicates they are **under 18**, they are flagged as `⚠️ Flagged by screening check (Under 18 / Review Needed)` in the Admin Channel so admins can decide manually.
* **🗑️ Instant Junk Reply Protection (Silent On-The-Spot Decline)**:
  * If a user replies with zero-effort nonsense, spam, or insults (`"who you are"`, `"yes yes yes"`, `"ok"`), the bot **silently declines their join request on the spot** without sending them a DM.
  * Sends an immediate report to the Admin Channel with the user's junk reply (`🗑️ Automatically Declined: Junk Reply`).
* **💬 Easy Admin Telegram Commands**:
  * `/reply <user_id> <message>` — Send a direct DM to an applicant directly from the Admin Channel.
  * `/decline <user_id> [reason]` — Decline a join request, send a decline reason DM, and delete screening DMs.
  * `/stats` — View real-time screening metrics for monitoring and CV reporting (total requests, passed, declined junk, timeout).
  * `/chat_id` — Get the Telegram ID of the current group or channel.
* **⏳ Rolling 48-Hour Timeout**:
  * Every time a message is sent, the 48-hour timer resets.
  * If an applicant goes unresponsive for 48 hours, their join request is automatically declined, DMs are deleted, and a timeout report is sent to admins.
* **📜 Permanent User History Tracking**:
  * Uses SQLite to permanently record user history (`PASSED_SCREENING`, `DISMISSED_TIMEOUT`, `APPROVED_JOINED`, `LEFT_GROUP`, `DISMISSED_ADMIN`).
  * Displays a history badge whenever a user re-applies.

---

## 📋 The 4 Screening Questions

1. **Are you Lebanese?**
2. **Are you 18 or older?** *(Must be 18+)*
3. **How did you find out about our server?**
4. **Why are you interested in joining?**

---

## 🚀 Setup & Installation

### 1. Requirements
* **Python 3.10+**
* A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
* A free Groq API key (`gsk_...`) from [console.groq.com](https://console.groq.com)

### 2. Clone & Install Dependencies
```bash
git clone <your-repo-url>
cd Tele\ bot

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the root directory (protected by `.gitignore`):

```ini
# Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

# Free Groq API Key (starts with gsk_)
AI_API_KEY="gsk_YourFreeGroqApiKeyHere"

# Telegram Chat ID of your Admin Channel/Group (use /chat_id to find it)
ADMIN_CHAT_ID="-1001234567890"

# Optional: Timeout in seconds (default is 172800 = 48 hours)
SCREENING_TIMEOUT_SECONDS=172800
```

---

## ⚠️ CRITICAL RULE: One Live Bot Instance at a Time

Telegram only allows **one live connection per bot token** at any given time.
* **100% Safe to Run Anytime**: You or any admin can run the test suites (`test_bot.py` and `test_real_llm.py`) locally on your computer at any time. They use mock Telegram objects and never interfere with a live bot.
* **Do Not Duplicate Live Hosting**: If you run `python bot.py` locally on your laptop, **make sure PythonAnywhere is stopped**, and vice-versa. Running `bot.py` in two places at the same time will cause a Telegram `Conflict: terminated by other getUpdates request` error.

---

## 🤖 Running the Bot

### Local Development / Foreground
```bash
venv/bin/python bot.py
```
You should see:
```text
INFO - R/lebanese Screening Bot is running. Waiting for join requests... (Ctrl+C to stop)
```

### Background Execution (Linux VPS / PythonAnywhere)
```bash
nohup venv/bin/python bot.py > bot.log 2>&1 &
```

---

## 🧪 Running the Test Suite

The project includes two comprehensive test suites:

### 1. Full Simulation Test Suite (`test_bot.py`)
Simulates the entire Telegram lifecycle without needing a live Telegram connection:
```bash
venv/bin/python test_bot.py
```
* **Scenario 1**: Join request arrives -> sends DM -> alerts admins -> starts 48h rolling timer.
* **Scenario 2**: Attempt #1 incomplete -> silently prompts user without alerting admins.
* **Scenario 3**: Attempt #2 -> pushes full 2-attempt conversation transcript to admins.
* **Scenario 4**: Admin uses `/reply <user_id> <msg>` -> relays custom DM to applicant.
* **Scenario 5**: 48-hour timeout fires -> auto-declines & deletes screening DMs.
* **Scenario 6**: User re-applies -> displays permanent user history badge.

### 2. Live Groq LLM & Clamp Stress Test Suite (`test_real_llm.py`)
Tests 10 real-world user replies against your live Groq API key:
```bash
venv/bin/python test_real_llm.py
```
* Tests polite paragraphs, casual short text, Lebanese Franco-Arabic dialect, under-18 flagging, and prompt-injection/jailbreak clamp resistance.

### 3. Junk vs. Incomplete Strict Separation Test Suite (`test_junk.py`)
Verifies that spam/nonsense replies are immediately declined as junk without affecting partial real answers:
```bash
venv/bin/python test_junk.py
```
* Proves that `"who you are"` or `"ok hello"` are classified as `JUNK`, while `"Lebanese, 21"` is classified as `INCOMPLETE` and never declined as junk.

> **Note on Production Safety (`TESTING_MODE`)**: All hardcoded test triggers (`TEST_INCOMPLETE`, `TEST_JUNK`, etc.) are wrapped in an environment check (`os.getenv("TESTING_MODE") == "1"`). In live production (`bot.py`), this variable is unset, guaranteeing that 100% of user replies are evaluated by the AI and no hardcoded test triggers can ever activate in production.

---

## 📂 Project Structure

```text
├── bot.py             # Main entry point and Telegram handler registration
├── config.py          # Configuration variables and hardcoded question strings
├── database.py        # SQLite database layer (sessions and permanent user history)
├── evaluator.py       # Groq AI classifier with clamp guardrails and anti-leniency rules
├── handlers.py        # Telegram event handlers (join requests, DMs, admin commands)
├── test_bot.py        # 6-scenario Telegram lifecycle simulation test suite
├── test_real_llm.py   # 10-scenario live Groq LLM & clamp stress test suite
├── test_junk.py       # 5-scenario JUNK vs INCOMPLETE strict separation test suite
├── requirements.txt   # Python package dependencies
├── .gitignore         # Protects secrets (.env, database, venv) from git
└── README.md          # Documentation
```
