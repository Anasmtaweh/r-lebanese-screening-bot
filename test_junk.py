#!/usr/bin/env python3
"""
test_junk.py — Automated test suite verifying strict separation between JUNK replies and INCOMPLETE answers.
Proves that spam/nonsense replies ("who you are", "yes yes yes") are classified as JUNK,
while partial real answers ("Lebanese, 22") are classified as INCOMPLETE and never declined as junk.
"""

import os
import time
from dotenv import load_dotenv
from evaluator import (
    AnswerEvaluator,
    RESULT_JUNK,
    RESULT_INCOMPLETE,
    RESULT_SATISFACTORY,
)

load_dotenv()


def run_junk_tests():
    api_key = os.getenv("AI_API_KEY", "")
    if not api_key:
        print("❌ ERROR: AI_API_KEY is not set in .env")
        return

    print("====================================================================")
    print("  STARTING JUNK vs. INCOMPLETE STRICT SEPARATION TEST SUITE")
    print(f"  API Key: {api_key[:8]}... | Model: llama-3.1-8b-instant")
    print("====================================================================\n")

    evaluator = AnswerEvaluator(api_key=api_key)

    scenarios = [
        {
            "id": 1,
            "title": "Zero-Effort Nonsense ('who you are')",
            "text": "who you are",
            "expected_res": RESULT_JUNK,
            "note": "Must classify as JUNK -> silent decline on the spot, no DM to user.",
        },
        {
            "id": 2,
            "title": "Lazy Repetitive Spam ('yes yes yes')",
            "text": "yes yes yes",
            "expected_res": RESULT_JUNK,
            "note": "Must classify as JUNK -> zero questions answered.",
        },
        {
            "id": 3,
            "title": "One-Word Greeting ('ok hello')",
            "text": "ok hello",
            "expected_res": RESULT_JUNK,
            "note": "Must classify as JUNK -> no screening info provided.",
        },
        {
            "id": 4,
            "title": "Real Partial Answer (Nationality + Age only)",
            "text": "Lebanese from Beirut, 21 years old",
            "expected_res": RESULT_INCOMPLETE,
            "note": "Must classify as INCOMPLETE -> real applicant who missed questions 3 & 4. Must NEVER be JUNK!",
        },
        {
            "id": 5,
            "title": "Real Partial Answer (Source only)",
            "text": "I found out about you guys from reddit r/lebanon",
            "expected_res": RESULT_INCOMPLETE,
            "note": "Must classify as INCOMPLETE -> answered Q3, missed Q1, Q2, Q4. Must NEVER be JUNK!",
        },
    ]

    passed_count = 0

    for sc in scenarios:
        print(f"--- SCENARIO {sc['id']}: {sc['title']} ---")
        print(f"📝 User Reply: \"{sc['text']}\"")
        print(f"📌 Note: {sc['note']}")

        start_time = time.time()
        res_type, feedback = evaluator.evaluate(sc["text"])
        duration = time.time() - start_time

        print(f"⏱️  Groq Latency: {duration:.2f} seconds")
        print(f"🤖 AI Classification: [{res_type}] (Expected: [{sc['expected_res']}])")

        if res_type == sc["expected_res"]:
            print(f"✅ SCENARIO {sc['id']} PASSED! Correctly separated.")
            passed_count += 1
        else:
            print(f"❌ SCENARIO {sc['id']} FAILED! Got {res_type} instead of {sc['expected_res']}")

        print("-" * 68 + "\n")

    print("====================================================================")
    print(f"  RESULTS: {passed_count} / {len(scenarios)} SCENARIOS PASSED")
    print("====================================================================")


if __name__ == "__main__":
    run_junk_tests()
