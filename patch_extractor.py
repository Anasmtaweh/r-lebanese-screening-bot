import sys
import re

with open("handlers.py", "r") as f:
    content = f.read()

helper = """
def _extract_target_id(update, context) -> int:
    \"\"\"Extracts the target user_id from either command args or the replied-to message.\"\"\"
    args = context.args or []
    if args and args[0].lstrip("-").isdigit():
        return int(args[0])
    
    if update.message and update.message.reply_to_message and update.message.reply_to_message.text:
        import re
        match = re.search(r"ID:\\s*`?(\\d+)`?", update.message.reply_to_message.text)
        if match:
            return int(match.group(1))
    return None
"""

if "_extract_target_id" not in content:
    content = content.replace("def _format_user_string(target_user_id: int) -> str:", helper + "\n\ndef _format_user_string(target_user_id: int) -> str:")

# Update _start_probation
content = re.sub(
    r'    if len\(args\) < 1 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /probation_<lang> <user_id>"\)\n        return\n    \n    target_user_id = int\(args\[0\]\)',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /probation_<lang> or type /probation_<lang> <user_id>")\n        return',
    content
)

# Update on_admin_reply_command
content = re.sub(
    r'    if len\(args\) < 2 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /reply <user_id> <message>"\)\n        return\n\n    target_user_id = int\(args\[0\]\)\n    msg_text = " "\.join\(args\[1:\]\)',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /reply <message> or type /reply <user_id> <message>")\n        return\n    \n    # If the first arg is the ID, message text starts at args[1], else args[0]\n    if args and args[0] == str(target_user_id):\n        msg_text = " ".join(args[1:])\n    else:\n        msg_text = " ".join(args)',
    content
)

# Update on_admin_decline_command
content = re.sub(
    r'    args = context\.args or \[\]\n    if len\(args\) < 1 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /decline <user_id> \[reason\]"\)\n        return\n\n    target_user_id = int\(args\[0\]\)\n    reason = " "\.join\(args\[1:\]\) if len\(args\) > 1 else ""',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /decline [reason] or type /decline <user_id> [reason]")\n        return\n    \n    args = context.args or []\n    if args and args[0] == str(target_user_id):\n        reason = " ".join(args[1:])\n    else:\n        reason = " ".join(args)',
    content
)

# Update on_admin_clear_command
content = re.sub(
    r'    args = context\.args or \[\]\n    if len\(args\) < 1 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /clear <user_id>"\)\n        return\n    \n    target_user_id = int\(args\[0\]\)',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /clear or type /clear <user_id>")\n        return',
    content
)

# Update on_admin_transcript_command
content = re.sub(
    r'    args = context\.args or \[\]\n    if len\(args\) != 1 or not args\[0\]\.lstrip\("-"\)\.isdigit\(\):\n        await update\.message\.reply_text\("Usage: /transcript <user_id>"\)\n        return\n\n    target_user_id = int\(args\[0\]\)',
    r'    target_user_id = _extract_target_id(update, context)\n    if not target_user_id:\n        await update.message.reply_text("Usage: Reply to a bot message with /transcript or type /transcript <user_id>")\n        return',
    content
)

with open("handlers.py", "w") as f:
    f.write(content)

