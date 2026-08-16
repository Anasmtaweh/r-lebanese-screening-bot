import json
import os
from typing import Tuple
import httpx

# Result Constants
RESULT_SATISFACTORY = "SATISFACTORY"
RESULT_INCOMPLETE = "INCOMPLETE"
RESULT_UNSATISFACTORY = "UNSATISFACTORY"
RESULT_JUNK = "JUNK"


class AnswerEvaluator:
    """
    Evaluates a user's reply to the screening questions.
    Returns a tuple: (result_type, feedback_or_summary)
    - result_type: SATISFACTORY, INCOMPLETE, UNSATISFACTORY, or JUNK
    - feedback_or_summary: Explanation for admins or follow-up prompt for user
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("AI_API_KEY", "")

    def evaluate(self, user_text: str) -> Tuple[str, str]:
        """
        Main evaluation entry point.
        Checks explicit test triggers first, then uses Groq/Gemini LLM evaluation if AI_API_KEY is configured,
        otherwise uses our smart multi-criteria rule-based evaluator.
        """
        if os.getenv("TESTING_MODE") == "1":
            text_upper = user_text.strip().upper()
            if "TEST_JUNK" in text_upper:
                return (RESULT_JUNK, user_text)
            if "TEST_UNSATISFACTORY" in text_upper:
                return (
                    RESULT_UNSATISFACTORY,
                    "User response was flagged as unsatisfactory or ineligible.",
                )
            if "TEST_INCOMPLETE" in text_upper:
                return (
                    RESULT_INCOMPLETE,
                    (
                        "Thank you for replying! However, it looks like some questions were unanswered.\n"
                        "Please make sure to answer all 4 questions:\n"
                        "1. Are you Lebanese?\n"
                        "2. Are you 18 or over?\n"
                        "3. How did you find out about our server?\n"
                        "4. Why are you interested in joining?"
                    ),
                )
            if "TEST_SATISFACTORY" in text_upper:
                return (RESULT_SATISFACTORY, user_text)

        if self.api_key:
            try:
                return self.evaluate_with_llm(user_text)
            except Exception as e:
                print(f"LLM evaluation failed ({e}), falling back to rule-based evaluation.")

        return self.evaluate_rule_based(user_text)

    def evaluate_rule_based(self, user_text: str) -> Tuple[str, str]:
        """
        Smart rule-based evaluator that checks if all 4 required criteria are addressed:
        1. Lebanese identity
        2. Age 18+
        3. How they found out
        4. Why they want to join
        """
        text_lower = user_text.strip().lower()

        # 2. Check for obvious under-age indicators
        words = text_lower.split()
        if "not 18" in text_lower or "under 18" in text_lower or "17" in words or "16" in words or "15" in words:
            return (
                RESULT_UNSATISFACTORY,
                "User indicated they are under 18 years old.",
            )

        # 3. Check for extremely short or lazy answers (e.g. "yes yes yes from Google to no people")
        if len(words) < 12:
            return (
                RESULT_INCOMPLETE,
                (
                    "Thank you for your reply! However, your response is too brief to evaluate properly.\n\n"
                    "Please provide a complete answer to all 4 questions:\n"
                    "1. Are you Lebanese?\n"
                    "2. Are you 18 or over?\n"
                    "3. How did you find out about our server?\n"
                    "4. Why are you interested in joining?"
                ),
            )

        # 4. Question-by-Question Coverage Heuristic:
        # Check if the reply touches on:
        # - Lebanese: "yes", "lebanese", "lebanon", "beirut", "tripoli", "lb", etc.
        # - Age: "18", "19", "20", "21", "22", "23", "24", "25", "30", "yes", "over 18", "years old"
        # - Found out: "reddit", "google", "friend", "r/lebanon", "server", "telegram", "link", "search", "found"
        # - Why join: "community", "people", "talk", "chat", "discuss", "news", "join", "friends", "lebanese", "culture"
        has_lebanese = any(kw in text_lower for kw in ["yes", "lebanese", "lebanon", "beirut", "lb", "am lebanese"])
        has_age = any(kw in text_lower for kw in ["18", "19", "20", "21", "22", "23", "24", "25", "30", "years", "old", "over 18"])
        has_source = any(kw in text_lower for kw in ["reddit", "google", "friend", "r/lebanon", "server", "telegram", "search", "found", "sub"])
        has_reason = any(kw in text_lower for kw in ["community", "people", "talk", "chat", "discuss", "news", "join", "friends", "connect", "know", "live"])

        missing = []
        if not has_lebanese:
            missing.append("1. Whether you are Lebanese")
        if not has_age:
            missing.append("2. Whether you are 18 or older")
        if not has_source:
            missing.append("3. How you found out about our server")
        if not has_reason:
            missing.append("4. Why you are interested in joining")

        if missing:
            missing_text = "\n".join(f"• {m}" for m in missing)
            return (
                RESULT_INCOMPLETE,
                f"Thank you for your response! However, it looks like you missed or didn't clearly answer the following:\n\n{missing_text}\n\nPlease reply with your complete answers so we can review your request!"
            )

        return (RESULT_SATISFACTORY, user_text)

    def evaluate_with_llm(self, user_text: str) -> Tuple[str, str]:
        """
        Calls Groq API (free Llama-3.1-8B) or Gemini API as a SILENT BACKEND CLASSIFIER.
        The LLM never communicates with the user or generates text for the user.
        It only classifies the response as SATISFACTORY, INCOMPLETE, or UNSATISFACTORY.
        """
        prompt = (
            "Analyze if the user answered ALL 4 screening questions:\n"
            "1. Are you Lebanese?\n"
            "2. Are you 18 or older? (Must be 18+)\n"
            "3. How did you find out about our server?\n"
            "4. Why are you interested in joining?\n\n"
            "CRITICAL RULES:\n"
            "1. JUNK vs INCOMPLETE: If the user did NOT genuinely answer ANY of the 4 screening questions (e.g. 'yes yes yes', 'ok hello', 'who you are', or spam), you MUST return JUNK!\n"
            "2. ANTI-LENIENCY: Only return INCOMPLETE if they genuinely answered AT LEAST ONE question (e.g., 'Lebanese, 22') but missed others. If any of the 4 questions is missing, you MUST return INCOMPLETE and NOT SATISFACTORY.\n"
            "3. DIALECTS: Accept answers in English, Arabic, or Lebanese Franco-Arabic dialect (e.g., 'eh lebanese akid, 25 sene').\n\n"
            f"User Reply:\n\"\"\"{user_text}\"\"\"\n\n"
            "Reply with exactly ONE line:\n"
            "- SATISFACTORY (if all 4 questions are explicitly answered)\n"
            "- UNSATISFACTORY (ONLY if the user indicates their age is UNDER 18, e.g. 15, 16, 17, or abusive)\n"
            "- JUNK (if 0 questions were answered, e.g. 'who you are', 'yes yes yes', 'ok hello', 'hello')\n"
            "- INCOMPLETE | <missing_numbers> (if 1-3 questions were answered, list ONLY the missing numbers separated by commas, e.g., 'INCOMPLETE | 3, 4')"
        )

        reply_token = ""
        with httpx.Client(timeout=10.0) as client:
            if self.api_key.startswith("gsk_"):
                # Groq API (Llama 3.1 8B Instant - 100% Free)
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 15,
                }
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                reply_token = data["choices"][0]["message"]["content"].strip().upper()
            else:
                # Gemini API
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 15},
                }
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                reply_token = data["candidates"][0]["content"]["parts"][0]["text"].strip().upper()

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
                1: "1. Are you Lebanese?",
                2: "2. Are you 18 or over?",
                3: "3. How did you find out about our server?",
                4: "4. Why are you interested in joining?",
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
            missing_lines = "\n".join(f"• {questions_map[n]}" for n in missing_nums)

            return (
                RESULT_INCOMPLETE,
                (
                    "Thank you for replying! However, it looks like we still need your answer for:\n\n"
                    f"{missing_lines}\n\n"
                    "Please reply with your answers so we can review your request!"
                ),
            )
