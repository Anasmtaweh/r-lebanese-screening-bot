import sys
import os

# Ensure we can import from the project directory
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from evaluator import AnswerEvaluator, RESULT_SATISFACTORY, RESULT_INCOMPLETE, RESULT_UNSATISFACTORY, RESULT_JUNK
from dotenv import load_dotenv

load_dotenv()

def print_result(name, expected_type, result):
    res_type, res_text = result
    if res_type == expected_type:
        print(f"✅ {name} PASSED! (Got {res_type})")
        if res_type == RESULT_INCOMPLETE:
            print(f"   Missing output: {repr(res_text)}")
    else:
        print(f"❌ {name} FAILED!")
        print(f"   Expected: {expected_type}, Got: {res_type}")
        print(f"   Text: {res_text}")

print("=== STARTING EXCESSIVE ARABIC TESTING ===\n")

# ==========================================
# 1. AI EVALUATION IN ARABIC
# ==========================================
print("--- TEST SUITE 1: LLM ARABIC EVALUATION ---")
# Create evaluator with real API key
ai_eval = AnswerEvaluator()

if not ai_eval.api_key:
    print("⚠️ WARNING: AI_API_KEY is not set in your .env file! AI tests will fallback.")
else:
    print(f"Using API Key starting with: {ai_eval.api_key[:8]}...")

# 1A. Perfect Arabic Answer
text_1a = "نعم لبناني عمري 22 سنة عرفت عن السيرفر من قوقل وأريد الانضمام بدون سبب محدد"
print("Testing: Valid Answer")
res_1a = ai_eval.evaluate(text_1a, language_code="ar")
print_result("Valid Answer (All 4)", RESULT_SATISFACTORY, res_1a)

# 1B. Missing Reason and Source
text_1b = "نعم انا لبناني وعمري 25"
print("Testing: Incomplete Answer (Missing 3 & 4)")
res_1b = ai_eval.evaluate(text_1b, language_code="ar")
print_result("Incomplete Answer", RESULT_INCOMPLETE, res_1b)

# 1C. Under 18
text_1c = "نعم لبناني عمري 16 سنة لقيتكم بريدت وبدي شارك"
print("Testing: Under 18")
res_1c = ai_eval.evaluate(text_1c, language_code="ar")
print_result("Under 18 Answer", RESULT_UNSATISFACTORY, res_1c)

# 1D. Junk / Spam
text_1d = "مرحبا كيفكم"
print("Testing: Junk Answer")
res_1d = ai_eval.evaluate(text_1d, language_code="ar")
print_result("Junk Answer", RESULT_JUNK, res_1d)

print("\n")

# ==========================================
# 2. RULE-BASED FALLBACK IN ARABIC
# ==========================================
print("--- TEST SUITE 2: RULE-BASED FALLBACK ARABIC EVALUATION ---")
# Force the fallback by removing API key
fallback_eval = AnswerEvaluator(api_key="")

# 2A. Perfect Arabic Answer (Using keywords)
text_2a = "نعم لبناني عمري 22 سنة عرفت عن السرفر من قوقل واريد الانضمام لسبب تفاعل معكم"
print("Testing: Valid Fallback Answer")
res_2a = fallback_eval.evaluate(text_2a, language_code="ar")
print_result("Valid Fallback Answer", RESULT_SATISFACTORY, res_2a)

# 2B. Missing Reason
text_2b = "اي لبناني عمري عشرين عام لقيتكم صدفة في تيك توك"
print("Testing: Incomplete Fallback (Missing 4)")
res_2b = fallback_eval.evaluate(text_2b, language_code="ar")
print_result("Incomplete Fallback (Missing Reason)", RESULT_INCOMPLETE, res_2b)

# 2C. Short answer (< 4 words)
text_2c = "نعم عمري ٢٠"
print("Testing: Short Answer (< 4 words)")
res_2c = fallback_eval.evaluate(text_2c, language_code="ar")
print_result("Short Fallback Answer", RESULT_INCOMPLETE, res_2c)

# 2D. Under 18
text_2d = "نعم عمري 17"
print("Testing: Under 18 Fallback")
res_2d = fallback_eval.evaluate(text_2d, language_code="ar")
print_result("Under 18 Fallback", RESULT_UNSATISFACTORY, res_2d)

print("\n=== TESTING COMPLETE ===")
