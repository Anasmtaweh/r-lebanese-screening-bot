
import os
import re
import asyncio
from typing import Tuple, Optional
from google import genai
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
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.test_mode = os.getenv("TESTING_MODE") == "1"

    async def evaluate(self, user_text: str, language_code: str = "en") -> Tuple[str, str, bool, Optional[str]]:
        """
        Main entry point for evaluating a user's reply.
        Prioritizes LLM if available, falls back to rule-based.
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
        zionist_keywords = ["israel", "israeli", "zionist", "zionism", "tel aviv", "idf", "צהל", "ישראל", "ישראלי", "ציוני", "صهيوني", "صهيونية", "اسرائيلي", "إسرائيلي", "إسرائيل", "اسرائيل"]
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
        if self.api_key:
            try:
                res, msg = await self.evaluate_with_llm(user_text, language_code)
                return (res, msg, True, None)
            except Exception as e:
                ai_error_msg = str(e)
                print(f"LLM evaluation failed ({e}), falling back to rule-based evaluation.")

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

    async def evaluate_with_llm(self, user_text: str, language_code: str = "en") -> Tuple[str, str]:
        """
        Calls Google Gemini API (gemini-3.6-flash) as a SILENT BACKEND CLASSIFIER.
        The LLM never communicates with the user or generates text for the user.
        It only classifies the response as SATISFACTORY, INCOMPLETE, or UNSATISFACTORY.
        """
        prompt = (
            "Analyze if the user answered ALL 4 screening questions:\n"
            "1. Are you Lebanese? If not, what country are you from? (Any nationality is accepted, we just need to know)\n"
            "2. Are you 18 or older? (A simple 'Yes', 'نعم', or an age >= 18 is acceptable)\n"
            "3. How did you find out about our server? (e.g. Telegram, Reddit, a friend, search)\n"
            "4. Why are you interested in joining?\n\n"
            "CRITICAL RULES:\n"
            "1. JUNK vs INCOMPLETE: If the user did NOT genuinely answer ANY of the 4 screening questions (e.g. 'yes yes yes', 'ok hello', 'who you are', or spam), you MUST return JUNK!\n"
            "2. ANTI-LENIENCY: Only return INCOMPLETE if they genuinely answered AT LEAST ONE question (e.g., 'Lebanese, 22') but missed others. If any of the 4 questions is missing, you MUST return INCOMPLETE and NOT SATISFACTORY.\n"
            "3. DIALECTS & SLANG: Accept answers in English, Arabic, or Lebanese Franco-Arabic dialect. Recognize modern AI tools like ChatGPT ('شات جي بتي') or internet search ('من النت') as valid sources for Q3. Recognize that insults or dismissals like 'انت مالك' (None of your business) do NOT answer Q4.\n"
            "4. HEBREW/ZIONIST: If the user writes in Hebrew script, mentions Israel as their country, or identifies as Zionist/Israeli, you MUST return UNSATISFACTORY immediately. This is an anti-Zionist community.\n\n"
            f"User Reply:\n\"\"\"{user_text}\"\"\"\n\n"
            "Reply with exactly ONE line:\n"
            "- SATISFACTORY (if all 4 questions are explicitly answered)\n"
            "- UNSATISFACTORY (if the user is under 18, writes in Hebrew, or identifies as Israeli/Zionist)\n"
            "- JUNK (if 0 questions were answered, e.g. 'who you are', 'yes yes yes', 'ok hello', 'hello')\n"
            "- INCOMPLETE | <missing_numbers> (if 1-3 questions were answered, list ONLY the missing numbers separated by commas, e.g., 'INCOMPLETE | 3, 4')"
        )

        # Initialize Gemini Client
        client = genai.Client(api_key=self.api_key)
        
        # Route API calls through PythonAnywhere proxy if applicable
        if os.environ.get("PYTHONANYWHERE_SITE"):
            client = genai.Client(
                api_key=self.api_key, 
                http_options={'proxy': 'http://proxy.server:3128'}
            )
            
        max_retries = 3
        resp = None
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                # Use the asynchronous aio client so we don't block the bot!
                resp = await client.aio.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=prompt,
                )
                break  # Success, exit the retry loop
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** (attempt + 1))  # Sleep 2s, then 4s non-blocking
                
        if resp is None:
            raise last_exception
            
        if not resp.text:
            raise ValueError("Gemini returned an empty response.")
            
        reply_token = resp.text.strip().upper()

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
