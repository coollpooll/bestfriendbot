import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
from openai import AsyncOpenAI
import asyncio

from config import *
from database import Database

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = Database(DATABASE_URL)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

MAX_REQUESTS = 3

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer("👋 Привет! Я Best Friend — твой ИИ-бот. Задай мне любой вопрос или отправь голосовое!")

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    await db.add_user(user_id)
    requests = await db.get_requests_today(user_id)
    if requests >= MAX_REQUESTS and user_id != OWNER_CHAT_ID:
        return await message.answer(
            "Твои бесплатные запросы закончились. Не хочешь ждать до завтра? Подключи подписку и получи полный доступ к ИИ! 🚀"
        )
    await db.increment_request(user_id)
    # GPT-4o ответ
    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": message.text}
        ],
        tools=[],
        tool_choice=None,
        max_tokens=700,
        user=str(user_id)
    )
    answer = resp.choices[0].message.content
    await message.answer(answer)

async def on_startup():
    await db.connect()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(on_startup())
    dp.run_polling(bot)
