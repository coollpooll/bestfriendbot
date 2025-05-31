import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
import aiofiles
from openai import OpenAI
import httpx

load_dotenv()

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID"))

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Database ---
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

    async def save_history(self, user_id, prompt, answer):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO usage_log (user_id, prompt, answer) VALUES ($1, $2, $3)",
                user_id, prompt, answer
            )

db = Database(DATABASE_URL)

# --- /start ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer("Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!")

# --- Main text handler ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    prompt = message.text.strip()

    try:
        # GPT-4o (через OpenAI Assistants API, если нужно – тут делаем через обычный chat/completions)
        response = await get_openai_response(prompt)
        answer = response.strip() if response else None

        logging.info(f"OpenAI ответил: {answer!r}")

        # Защитимся от пустых/стандартных/бесполезных ответов
        if answer and len(answer) > 5 and not answer.lower().startswith("как виртуальный ассистент"):
            await message.answer(answer)
            await db.save_history(user_id, prompt, answer)
        else:
            await message.answer("Что-то пошло не так, попробуй ещё раз.")
    except Exception as e:
        logging.exception(e)
        await message.answer("Ошибка при получении ответа от ИИ 🤖")

# --- Запрос к OpenAI (async) ---
async def get_openai_response(prompt: str) -> str:
    async with httpx.AsyncClient() as session:
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        data = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = await session.post("https://api.openai.com/v1/chat/completions", json=data, headers=headers, timeout=30)
        resp.raise_for_status()
        completion = resp.json()
        return completion["choices"][0]["message"]["content"]

# --- FastAPI events ---
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






