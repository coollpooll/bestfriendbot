import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
from openai import AsyncOpenAI

load_dotenv()

# Логирование для отладки
logging.basicConfig(level=logging.INFO)

# Telegram bot token и др.
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))

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
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                user_id
            )

db = Database(DATABASE_URL)

# OpenAI Client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer("Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!")

# Ответ на любое текстовое сообщение через GPT-4o
@dp.message()
async def ai_answer(message: types.Message):
    if message.text:
        # Отправляем запрос в OpenAI Assistants API
        thread = await openai_client.beta.threads.create_and_run(
            assistant_id=ASSISTANT_ID,
            thread={"messages": [{"role": "user", "content": message.text}]}
        )
        # Достаём финальный ответ, можно доработать если нужно потоковое
        try:
            # Стандартный способ получения ответа
            result = thread.latest_run.step_details['message']['content'][0]['text']['value']
        except Exception:
            result = "🤖 Готово! Но что-то пошло не так с получением ответа от GPT."
        await message.answer(result)

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


