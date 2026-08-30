import sys
import os
from evaluator import AnswerEvaluator
from dotenv import load_dotenv

load_dotenv()
evaluator = AnswerEvaluator()
try:
    res, txt = evaluator.evaluate("Lebanese, 22 years old, from reddit, to make friends")
    print("SUCCESS", res, txt)
except Exception as e:
    print("ERROR", e)
