import sys
import re

with open("handlers.py", "r") as f:
    content = f.read()

# Update on_admin_decline_command
content = re.sub(
    r'    args = context\.args or \[\]\n    if len\(args\) < 1 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /decline <user_id> \[reason\]"\)\n        return\n\n    target_user_id = int\(args\[0\]\)\n    reason = " "\.join\(args\[1:\]\) if len\(args\) > 1 else ""',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /decline [reason] or type /decline <user_id> [reason]")\n        return\n    \n    args = context.args or []\n    if args and args[0] == str(target_user_id):\n        reason = " ".join(args[1:])\n    else:\n        reason = " ".join(args)',
    content, count=1
)

# Update on_admin_transcript_command
content = re.sub(
    r'    args = context\.args or \[\]\n    if len\(args\) != 1 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /transcript <user_id>"\)\n        return\n\n    target_user_id = int\(args\[0\]\)',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /transcript or type /transcript <user_id>")\n        return',
    content, count=1
)

with open("handlers.py", "w") as f:
    f.write(content)

