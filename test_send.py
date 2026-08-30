import asyncio
from telegram import Bot
from config import BOT_TOKEN

async def main():
    bot = Bot(token=BOT_TOKEN)
    try:
        # We can't really send to a random user without their chat_id and them starting the bot.
        # But we can get bot info.
        me = await bot.get_me()
        print("Bot info:", me.username)
    except Exception as e:
        print("Error:", e)

asyncio.run(main())
