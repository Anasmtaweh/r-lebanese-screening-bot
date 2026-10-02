
import os
import re
import asyncio
from typing import Tuple, Optional
import httpx
from google import genai
from google.genai import types
from config import INCOMPLETE_PROMPT_EN, INCOMPLETE_PROMPT_AR

# Result Constants
RESULT_SATISFACTORY = "SATISFACTORY"
RESULT_INCOMPLETE = "INCOMPLETE"
RESULT_UNSATISFACTORY = "UNSATISFACTORY"
RESULT_JUNK = "JUNK"


class AnswerEvaluator:
    """
    Evaluates a user's reply to the screening questions.
    Returns a tuple: (result_type, feedback_or_summary, was_ai_used, ai_error_msg)
    - result_type: SATISFACTORY, INCOMPLETE, UNSATISFACTORY, or JUNK
    - feedback_or_summary: Explanation for admins or follow-up prompt for user
    - was_ai_used: Boolean indicating if the AI (LLM) was used for evaluation
    - ai_error_msg: String containing the error message if the LLM failed, else None
    """

    def __init__(self, api_key: str = ""):
        self.groq_api_key = os.getenv("GROQ_API_KEY") or os.getenv("AI_API_KEY", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        if api_key:
            if api_key.startswith("gsk_"):
                self.groq_api_key = api_key
            else:
                self.gemini_api_key = api_key
                if not self.groq_api_key:
                    self.groq_api_key = api_key

        self.test_mode = os.getenv("TESTING_MODE") == "1"
        self.last_groq_model = ""
        self.last_gemini_model = ""

    @property
    def api_key(self) -> str:
        return self.groq_api_key or self.gemini_api_key

    async def evaluate(self, user_text: str, language_code: str = "en") -> Tuple[str, str, bool, Optional[str]]:
        """
        Main entry point for evaluating a user's reply.
        Prioritizes Groq (fast, free, high limit), falls back to Gemini, then rule-based.
        Returns: (result_type, feedback, was_ai_used, ai_error_msg)
        """
        # HARD GATE: Hebrew/Zionist detection runs BEFORE everything else.
        # This cannot be bypassed by the AI, test mode, or any other code path.
        if re.search(r'[\u0590-\u05FF]', user_text):
            return (
                RESULT_UNSATISFACTORY,
                "⚠️ FLAGGED: User replied in Hebrew. Requires admin review.",
                False,
                None
            )
        text_lower = user_text.strip().lower()
        zionist_keywords = ["israel", "israeli", "zionist", "zionism", "tel aviv", "idf", "צהל", "ישראל", "ישראلي", "ציוני", "صهيوني", "صهيونية", "اسرائيلي", "إسرائيلي", "إسرائيل", "اسرائيل"]
        if any(kw in text_lower for kw in zionist_keywords):
            return (
                RESULT_UNSATISFACTORY,
                "⚠️ FLAGGED: User mentioned Israel/Zionist affiliation. Requires admin review.",
                False,
                None
            )

        if self.test_mode:
            text_upper = user_text.strip().upper()
            if "TEST_JUNK" in text_upper:
                return (RESULT_JUNK, user_text, False, None)
            if "TEST_UNSATISFACTORY" in text_upper:
                return (
                    RESULT_UNSATISFACTORY,
                    "User response was flagged as unsatisfactory or ineligible.",
                    False,
                    None
                )
            if "TEST_SATISFACTORY" in text_upper:
                return (RESULT_SATISFACTORY, user_text, False, None)
            if "TEST_INCOMPLETE" in text_upper:
                prompt = INCOMPLETE_PROMPT_AR if language_code == "ar" else INCOMPLETE_PROMPT_EN
                return (
                    RESULT_INCOMPLETE,
                    prompt.format(missing_text="• All 4 questions / جميع الأسئلة الأربعة"),
                    False,
                    None
                )

        ai_error_msg = None

        # 1. Primary: Try Gemini first (as per architecture flowchart)
        if self.gemini_api_key:
            try:
                res, msg = await self.evaluate_with_gemini(user_text, language_code)
                return (res, msg, True, None)
            except Exception as e:
                ai_error_msg = f"Gemini error: {e}"
                print(f"Gemini evaluation failed ({e}), seamlessly switching to Groq fallback...")

        # 2. Seamless Fallback: Try Groq (Llama 3.1 8B Instant - 14,400 free/day, ~0.15s async)
        if self.groq_api_key:
            try:
                res, msg = await self.evaluate_with_groq(user_text, language_code)
                # Groq succeeded! Clear error so developer is not spammed with false failure alarms
                return (res, msg, True, None)
            except Exception as e:
                groq_err = f"Groq error: {e}"
                ai_error_msg = f"{ai_error_msg}; {groq_err}" if ai_error_msg else groq_err
                print(f"Groq evaluation failed ({e}), falling back to rule-based evaluation.")

        # 3. Final Fallback: Rule-based heuristic (only when both AI providers fail or are missing)
        res, msg = self.evaluate_rule_based(user_text, language_code)
        return (res, msg, False, ai_error_msg)


    def evaluate_rule_based(self, user_text: str, language_code: str = "en") -> Tuple[str, str]:
        """
        Smart rule-based evaluator that checks if all 4 required criteria are addressed:
        1. Lebanese identity
        2. Age 18+
        3. How they found out
        4. Why they want to join
        """
        text_lower = user_text.strip().lower()

        # 1. Check for obvious under-age indicators
        words = text_lower.split()
        if "not 18" in text_lower or "under 18" in text_lower or "17" in words or "16" in words or "15" in words:
            return (
                RESULT_UNSATISFACTORY,
                "User indicated they are under 18 years old.",
            )

        # 2. Check for extremely short or lazy answers (e.g. "yes" or "ok")
        if len(words) < 4:
            prompt = INCOMPLETE_PROMPT_AR if language_code == "ar" else INCOMPLETE_PROMPT_EN
            return (
                RESULT_INCOMPLETE,
                prompt.format(missing_text="• All 4 questions / جميع الأسئلة الأربعة")
            )

        # 3. Question-by-Question Coverage Heuristic:
        has_nationality = any(kw in text_lower for kw in ["lebanese", "lebanon", "beirut", "lb", "am lebanese", "لبناني", "لبنانية", "لبنان", "بيروت", "not lebanese", "from", "country", "syrian", "iraqi", "egyptian", "jordanian", "palestinian", "iranian", "سوري", "عراقي", "مصري", "أردني", "فلسطيني", "إيراني", "إيرانية", "سورية", "مصرية", "بلد", "جنسية", "من", "سعود", "مغرب", "جزائر", "تونس", "كويت", "قطر", "امارات", "عمان", "يمن", "سودان", "صومال", "ليبيا"])
        
        # Check for numeric age >= 18 or text age keywords (including "yes"/"نعم" as valid age confirmations)
        has_numeric_age = False
        for num_str in re.findall(r'\b\d{2}\b', user_text):
            if int(num_str) >= 18:
                has_numeric_age = True
                break
        has_age = has_numeric_age or any(kw in text_lower for kw in ["yes", "نعم", "اي", "يب", "أجل", "no", "years", "old", "over 18", "عشرين", "سنة", "عمري", "عام", "عمر", "فوق"])
        
        has_source = any(kw in text_lower for kw in ["reddit", "google", "friend", "r/lebanon", "server", "telegram", "search", "found", "sub", "ريدت", "قوقل", "جوجل", "صديق", "صاحب", "صدق", "اصدقاء", "صاحبي", "بحث", "صدفة", "تيك توك", "تليجرام", "تيليغرام", "رابط", "chatgpt", "chat gpt", "شات جي بي تي", "ai", "ذكاء", "اصطناعي", "من النت", "نت"])
        has_reason = any(kw in text_lower for kw in ["community", "people", "talk", "chat", "discuss", "news", "join", "friends", "connect", "know", "live", "اتحدث", "تعارف", "دردشة", "انضمام", "انضم", "استمتع", "سبب", "تفاعل", "فضول", "شوف", "اشوف", "حابب", "صداق", "لعب", "العب", "وقت", "استفاد"])

        missing = []
        if not has_nationality:
            missing.append("1. Your nationality / جنسيتك")
        if not has_age:
            missing.append("2. Whether you are 18 or older / هل عمرك 18 سنة أو أكثر")
        if not has_source:
            missing.append("3. How you found out about our server / كيف عرفت عن السيرفر")
        if not has_reason:
            missing.append("4. Why you are interested in joining / لماذا تريد الانضمام إلى السيرفر")

        if missing:
            missing_text = "\n".join(f"• {m}" for m in missing)
            prompt = INCOMPLETE_PROMPT_AR if language_code == "ar" else INCOMPLETE_PROMPT_EN
            return (
                RESULT_INCOMPLETE,
                prompt.format(missing_text=missing_text)
            )

        return (RESULT_SATISFACTORY, user_text)

    def _get_classification_prompt(self, user_text: str) -> str:
        return (
            "Analyze if the user answered ALL 4 screening questions:\n"
            "1. Are you Lebanese? If not, what country are you from? (Any nationality is accepted, we just need to know)\n"
            "2. Are you 18 or older? (A simple 'Yes', 'نعم', or an age >= 18 is acceptable)\n"
            "3. How did you find out about our server? (e.g. Telegram, Reddit, a friend, search)\n"
            "4. Why are you interested in joining?\n\n"
            "CRITICAL RULES:\n"
            "1. JUNK vs INCOMPLETE: Single-word affirmative answers (e.g. 'yes', 'نعم', 'اي', 'أجل') or numbers (e.g. '22') ARE valid answers to Question 1 (nationality) or Question 2 (age). Do NOT classify these as JUNK! Only return JUNK if the user reply is completely non-responsive, gibberish (e.g. 'asdfgh'), off-topic spam ('buy crypto'), or insults ('who are you').\n"
            "2. ANTI-LENIENCY: If the user genuinely answered AT LEAST ONE question (including 'yes'/'نعم' for Q1/Q2), you MUST return INCOMPLETE listing the missing questions (e.g., 'INCOMPLETE | 3, 4'). Do NOT return JUNK or SATISFACTORY.\n"
            "3. DIALECTS & SLANG: Accept answers in English, Arabic, or Lebanese Franco-Arabic dialect. Recognize modern AI tools like ChatGPT ('شات جي بتي') or internet search ('من النت') as valid sources for Q3. Recognize that insults or dismissals like 'انت مالك' (None of your business) do NOT answer Q4.\n"
            "4. HEBREW/ZIONIST: If the user writes in Hebrew script, mentions Israel as their country, or identifies as Zionist/Israeli, you MUST return UNSATISFACTORY immediately. This is an anti-Zionist community.\n\n"
            f"User Reply:\n\"\"\"{user_text}\"\"\"\n\n"
            "Reply with exactly ONE line:\n"
            "- SATISFACTORY (if all 4 questions are explicitly answered)\n"
            "- UNSATISFACTORY (if the user is under 18, writes in Hebrew, or identifies as Israeli/Zionist)\n"
            "- JUNK (if 0 questions were answered, e.g. gibberish 'asdfgh', 'who are you', spam, insults)\n"
            "- INCOMPLETE | <missing_numbers> (if 1-3 questions were answered, list ONLY the missing numbers separated by commas, e.g., 'INCOMPLETE | 3, 4')"
        )

    def _parse_reply_token(self, reply_token: str, user_text: str, language_code: str = "en") -> Tuple[str, str]:
        if "SATISFACTORY" in reply_token and "UNSATISFACTORY" not in reply_token:
            return (RESULT_SATISFACTORY, user_text)
        elif "JUNK" in reply_token:
            return (RESULT_JUNK, user_text)
        elif "UNSATISFACTORY" in reply_token:
            # DO NOT auto-decline! Return for manual admin review
            return (
                RESULT_UNSATISFACTORY,
                "⚠️ Flagged by screening check: User indicated under 18 or review needed. Admins please review manually.",
            )
        else:
            # Parse missing question numbers from token (e.g., "INCOMPLETE | 2, 4")
            questions_map = {
                1: "1. Your nationality / جنسيتك",
                2: "2. Whether you are 18 or older / هل عمرك 18 سنة أو أكثر",
                3: "3. How you found out about our server / كيف عرفت عن السيرفر",
                4: "4. Why you are interested in joining / لماذا تريد الانضمام إلى السيرفر",
            }
            missing_nums = []
            for char in reply_token:
                if char in "1234":
                    num = int(char)
                    if num not in missing_nums:
                        missing_nums.append(num)
            if not missing_nums:
                missing_nums = [1, 2, 3, 4]
            missing_nums.sort()
            
            missing_text = "\n".join(f"• {questions_map[n]}" for n in missing_nums)
            prompt = INCOMPLETE_PROMPT_AR if language_code == "ar" else INCOMPLETE_PROMPT_EN
            
            return (
                RESULT_INCOMPLETE,
                prompt.format(missing_text=missing_text),
            )

    async def evaluate_with_groq(self, user_text: str, language_code: str = "en") -> Tuple[str, str]:
        """
        Calls Groq API with multi-model fallback using non-blocking async HTTP.
        """
        prompt = self._get_classification_prompt(user_text)
        models = [
            "llama-3.3-70b-versatile",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
            "llama-3.1-8b-instant"
        ]
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }

        proxy_url = "http://proxy.server:3128" if os.environ.get("PYTHONANYWHERE_SITE") else None
        last_exception = None
        reply_token = ""

        async with httpx.AsyncClient(proxy=proxy_url, timeout=10.0) as client:
            for model_name in models:
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 60,
                }
                for attempt in range(2):
                    try:
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            json=payload,
                            headers=headers,
                        )
                        if resp.is_success:
                            data = resp.json()
                            reply_token = data["choices"][0]["message"]["content"].strip().upper()
                            self.last_groq_model = model_name
                            break
                        else:
                            err_text = resp.text
                            last_exception = ValueError(f"Groq {model_name} HTTP {resp.status_code}: {err_text}")
                            # If client error (400, 404, etc.), skip to next model
                            if 400 <= resp.status_code < 500:
                                break
                    except Exception as e:
                        last_exception = e
                        if attempt < 1:
                            await asyncio.sleep(0.5)
                if reply_token:
                    break

        if not reply_token:
            raise last_exception or ValueError("Groq returned an empty response.")

        return self._parse_reply_token(reply_token, user_text, language_code)

    async def evaluate_with_gemini(self, user_text: str, language_code: str = "en") -> Tuple[str, str]:
        """
        Calls Google Gemini API (gemini-2.5-flash with gemini-1.5-flash and gemini-3.6-flash fallback).
        """
        prompt = self._get_classification_prompt(user_text)
        client = genai.Client(api_key=self.gemini_api_key)
        
        if os.environ.get("PYTHONANYWHERE_SITE"):
            client = genai.Client(
                api_key=self.gemini_api_key, 
                http_options={'proxy': 'http://proxy.server:3128'}
            )
            
        config = types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )

        models = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-3.6-flash']
        resp = None
        last_exception = None
        
        for model_name in models:
            try:
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                if resp and resp.text:
                    self.last_gemini_model = model_name
                    break
            except Exception as e:
                last_exception = e
                # If a model returns 503 UNAVAILABLE or 404, immediately try the next model
                continue
                
        if resp is None:
            raise last_exception
            
        if not resp.text:
            raise ValueError("Gemini returned an empty response.")
            
        reply_token = resp.text.strip().upper()
        return self._parse_reply_token(reply_token, user_text, language_code)

    async def evaluate_with_llm(self, user_text: str, language_code: str = "en") -> Tuple[str, str]:
        """Backward compatible wrapper that prefers Groq then Gemini."""
        if self.groq_api_key:
            return await self.evaluate_with_groq(user_text, language_code)
        elif self.gemini_api_key:
            return await self.evaluate_with_gemini(user_text, language_code)
        raise ValueError("No AI API key configured.")

