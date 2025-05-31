import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
from openai import OpenAI
import httpx
from pydub import AudioSegment
import speech_recognition as sr

load_dotenv()

logging.basicConfig(level=logging.INFO)

# ENV vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID"))

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# Подключение к базе данных
class Database:
    def __init__(self, dsn):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn)

    async def disconnect(self):
        if self.pool:
            await self.pool.close()

    async def add_user(self, user_id):
        async with self.pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO users (user_id, requests_today) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
                user_id, 0
            )

    async def inc_requests(self, user_id):
        async with self.pool.acquire() as connection:
            await connection.execute(
                "UPDATE users SET requests_today = requests_today + 1 WHERE user_id = $1", user_id
            )

    async def get_requests(self, user_id):
        async with self.pool.acquire() as connection:
            result = await connection.fetchrow(
                "SELECT requests_today FROM users WHERE user_id = $1", user_id
            )
            if result:
                return result['requests_today']
            else:
                return 0

db = Database(DATABASE_URL)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer(
        "Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!\n"
        "Поддерживаю текст, голос и картинки. /help для справки."
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Я бот на GPT-4o, умею:\n"
        "— Текстовые ответы (GPT-4o)\n"
        "— Голос (Whisper)\n"
        "— Картинки (DALL-E)\n"
        "Лимит: 3 запроса в сутки бесплатно.\n"
        "Подписка — неограниченно!\n"
        "/status — узнать остаток лимита."
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    count = await db.get_requests(message.from_user.id)
    await message.answer(f"Сегодня использовано запросов: {count}/3")

# ———  ОБРАБОТКА ЛЮБОГО ТЕКСТОВОГО СООБЩЕНИЯ ———
@dp.message()
async def gpt4o_reply(message: types.Message):
    user_id = message.from_user.id

    # 1. Лимит запросов
    requests_today = await db.get_requests(user_id)
    if requests_today >= 3:
        await message.answer("⛔ Лимит запросов на сегодня исчерпан!\nОформи подписку для безлимита.")
        return

    await db.inc_requests(user_id)

    # 2. GPT-4o обработка
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты дружелюбный ассистент."},
                {"role": "user", "content": message.text}
            ]
        )
        reply = response.choices[0].message.content
        await message.answer(reply)
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        await message.answer("🤖 Готово! Но что-то пошло не так с получением ответа от GPT.")

@app.on_event("startup")
async def on_startup():
    await db.connect()
    logging.info("Database connected")

@app.on_event("shutdown")
async def on_shutdown():
    await db.disconnect()
    logging.info("Database disconnected")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    body = await request.json()
    update = types.Update(**body)
    await dp.feed_update(bot, update)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)



