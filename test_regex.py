import re

texts = [
    "📋 *Satisfactory Screening Reply*\n👤 Anas (@anas)| ID: `123456789`\n💡 Use `/reply 123456789 <msg>` or approve/decline in Telegram.",
    "⚠️ *Flagged Screening Reply (Review Needed)*\n👤 En sh ha | ID: `5664417338`\n💡 Please review."
]

for text in texts:
    match = re.search(r"ID:\s*`?(\d+)`?", text)
    if match:
        print(f"Matched ID: {match.group(1)}")
    else:
        print("No match")
