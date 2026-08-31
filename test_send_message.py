import asyncio
from telegram import Bot
from config import BOT_TOKEN
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=BOT_TOKEN)
    chat_id = 8389197022
    admin_text = "This is a direct test reply."
    try:
        sent_msg = await bot.send_message(
            chat_id=chat_id,
            text=f"💬 Message from R/lebanese Admin:\n\n{admin_text}",
        )
        print("Success! Message ID:", sent_msg.message_id)
    except Exception as e:
        print("Failed:", e)

asyncio.run(main())
