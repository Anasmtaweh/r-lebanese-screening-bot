import requests
import json

url = "https://api.groq.com/openai/v1/chat/completions"
headers = {
    "Authorization": "Bearer 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",
    "Content-Type": "application/json"
}

payload1 = {
    "model": "llama3-8b-8192",
    "messages": [{"role": "user", "content": "hi"}]
}

resp = requests.post(url, headers=headers, json=payload1)
print(f"llama3-8b-8192: {resp.status_code} {resp.text}")

payload2 = {
    "model": "llama-3.1-8b-instant",
    "messages": [{"role": "user", "content": "hi"}]
}
resp2 = requests.post(url, headers=headers, json=payload2)
print(f"llama-3.1-8b-instant: {resp2.status_code} {resp2.text}")
