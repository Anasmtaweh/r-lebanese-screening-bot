import os
import httpx
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("AI_API_KEY")

with httpx.Client() as client:
    resp = client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {api_key}"})
    print(resp.status_code, resp.text)
