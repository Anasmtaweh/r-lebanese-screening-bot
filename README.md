# 🌲 R/lebanese Telegram Screening Bot

An autonomous, hardened Telegram screening and onboarding bot designed for the **R/lebanese** community. Built with [`python-telegram-bot` v21](https://github.com/python-telegram-bot/python-telegram-bot), PostgreSQL (Supabase connection pooling), and a high-availability **Dual-Provider AI Evaluator** (Google Gemini + Groq Llama 3.1 8B).

---

## 🛠️ Architecture & Pipeline

```mermaid
flowchart TD
    JoinReq["👤 User Requests to Join"] --> LangPrompt["🌐 Language Selection: EN / AR"]
    LangPrompt --> DMQuestions["📝 Bot Sends 4 Screening Questions in DM"]
    DMQuestions --> UserAnswers["💬 User Replies with Answers"]
    
    UserAnswers --> PreFilter{"1. Pre-AI Security Gate"}
    
    %% Branch A: Flagged by Pre-Filter
    PreFilter -->|Flagged / Ineligible| FlaggedAdmin["🚨 Bypasses AI Completely<br>Forward Flagged Report to Admins"]
    FlaggedAdmin --> AdminDecision{"Human Admin Decision"}

    %% Branch B: Passed to AI Pipeline
    subgraph EvaluationPipeline ["Dual-AI Evaluation Pipeline"]
        PreFilter -->|Passed Gate| AI_Gemini["2. Google Gemini - Primary Classifier"]
        AI_Gemini -->|Success| Decision{"Classification Result"}
        AI_Gemini -->|Quota Limit / 429| AI_Groq["3. Groq Llama 3.1 8B - Async Fallback"]
        AI_Groq -->|Success| Decision
        AI_Groq -->|Provider Outage| RuleBased["4. Deterministic Heuristic Fallback"]
        RuleBased --> Decision
    end

    Decision -->|Incomplete - Attempt 1| FollowUp["🔁 Silent Follow-Up - Ask for missing answers"]
    FollowUp --> UserAnswers
    Decision -->|Satisfactory or Attempt 2| AdminReview["📋 Forward Full Transcript to Admin Channel"]
    Decision -->|Under 18 Detected| Under18Review["⚠️ Flag as Under-18 for Admin Review"]
    
    AdminReview --> AdminDecision
    Under18Review --> AdminDecision
    AdminDecision -->|Approve| Accepted["✅ Approve User into Community"]
    AdminDecision -->|Decline| Declined["❌ Decline and Bulk Delete DMs"]
```

---

## ✨ Features & Capabilities

### 1. Dual-Provider AI Evaluation (High Availability)
* **Primary AI — Google Gemini:** High-precision evaluation of applicant answers across English, Modern Standard Arabic, and Lebanese Franco-Arabic dialects.
* **Secondary Fallback — Groq (`llama-3.1-8b-instant`):** Asynchronous, ultra-low latency (~0.15s) inference handling up to 14,400 daily requests. Seamlessly takes over if the primary model reaches quota limits.
* **Rule-Based Emergency Safety Net:** If both external AI providers are unavailable, the bot automatically falls back to an internal bilingual keyword and regex heuristic. The bot never halts screening.

### 2. The 4 Screening Criteria
Applicants must provide valid responses to 4 required criteria:
1. **Nationality / Origin** (Lebanese or specified country of origin)
2. **Age Verification** (Must explicitly confirm 18+)
3. **Discovery Channel** (How they found out about the community)
4. **Intent / Reason** (Why they wish to join)

### 3. "Silent Classifier" Architecture (Prompt-Injection Proof)
* The LLM operates strictly as an internal backend classifier that outputs a single constrained evaluation token.
* **The AI never generates text seen by users.** Users only ever receive pre-approved, hardcoded bilingual message templates from `config.py`.
* Zero prompt injection exposure, zero hallucination risk, and zero unintended conversational behavior.

### 4. 100% Human-In-The-Loop Control
The bot screens and assists, but **humans make every final decision**:
* The bot **never** automatically approves applicants into the group.
* Once answers are complete, the applicant's formatted transcript is forwarded to the private Admin Channel.
* Admins take action directly using interactive buttons or commands:
  * `/reply <user_id> <message>` — Send a DM to an applicant (with an interactive `[Undo ↩️]` button).
  * `/decline <user_id> [reason]` — Decline a join request, notify the user, and clean up chat history.
  * `/transcript <user_id>` — Retrieve complete historical conversation transcripts.
  * `/stats` — Real-time screening and moderation analytics dashboard.
  * `/list <category>` — Inspect recent applicants across 10+ status categories.

### 5. Automated Timers & Privacy Protection
* **Rolling 48-Hour Timeout:** Inactive join requests are automatically declined after 48 hours to keep queues clean.
* **Zero-Trace Message Cleanup:** When a user is declined or times out, the bot bulk-deletes all screening messages from the user's private DM.
* **1-Week Probation Tracking:** Approved members are passively monitored during their initial week, with an in-memory cache ensuring zero database overhead on high-velocity group messages.

---

## 🚀 Setup & Deployment

### 1. Environment Configuration (`.env`)
Create a `.env` file (or set variables in your cloud hosting provider):

```ini
# Telegram Bot Token (from @BotFather)
BOT_TOKEN="your_telegram_bot_token_here"

# AI Provider API Keys
GEMINI_API_KEY="your_gemini_api_key_here"
GROQ_API_KEY="your_groq_api_key_here"  # Or AI_API_KEY

# PostgreSQL Database (e.g. Supabase)
DATABASE_URL="postgresql://user:password@host:port/database"

# Admin Configuration
ADMIN_CHAT_ID="-100xxxxxxxxxx"
ADMIN_USER_IDS="123456789,987654321"

# Production Webhook & Monitoring (Optional for local testing)
RENDER_EXTERNAL_URL="https://your-bot.onrender.com"
HEALTHCHECK_URL="https://hc-ping.com/your-uuid-here"
```

### 2. Local Development (Polling Mode)
If `RENDER_EXTERNAL_URL` is omitted, the bot starts in Polling mode:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

### 3. Production Deployment (Webhook Mode on Render)
* Set `RENDER_EXTERNAL_URL` in your environment. The bot automatically starts an asynchronous Tornado web server binding to `0.0.0.0:$PORT`.
* To prevent Render free tier instances from sleeping, configure an external ping (e.g., `cron-job.org`) to send an HTTP GET request to your Render root URL every 10–14 minutes.

---

## 📂 Project Structure
```text
├── bot.py              # Application entry point, webhook/polling setup, and event routing
├── config.py           # Configuration constants, timeouts, and bilingual prompt templates
├── database.py         # Thread-safe PostgreSQL connection pooling and CRUD operations
├── evaluator.py        # Dual-provider AI classifier (Gemini + Groq) and rule-based fallback
├── handlers.py         # Telegram event handlers (Join requests, DMs, Admin commands)
└── requirements.txt    # Application dependencies
```
