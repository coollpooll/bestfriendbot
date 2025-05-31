import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
from openai import OpenAI
import httpx
import aiofiles

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
openai_client = OpenAI(api_key=OPENAI_API_KEY)

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

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer("Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!")

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

# ------------- Голосовой обработчик ------------------

@dp.message(lambda message: message.voice is not None)
async def handle_voice(message: types.Message):
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    local_path = f"/tmp/{file_id}.ogg"

    await bot.download_file(file_path, local_path)

    text = ""
    answer = "Ошибка при получении ответа от ИИ 🤖"
    try:
        # Открываем ogg-файл для Whisper
        with open(local_path, "rb") as audio_file:
            transcript = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                filename="voice.ogg"
            )
        text = transcript.strip()
        logging.info(f"Whisper распознал: '{text}'")
        if not text:
            await message.answer("Не смог распознать голос. Попробуй ещё раз!")
            return
        await message.answer(f"Ты сказал: <i>{text}</i>")
        # Отправляем в GPT
        completion = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": text}]
        )
        answer = completion.choices[0].message.content.strip()
        await message.answer(answer)
        # Сохраняем историю
        await db.save_history(message.from_user.id, text, answer)
    except Exception as e:
        logging.exception(f"Ошибка при обработке голосового: {e}")
        await message.answer("Ошибка при получении ответа от ИИ 🤖")
    finally:
        try:
            os.remove(local_path)
        except Exception:
            pass

# ------------- Текстовый обработчик ------------------

@dp.message(lambda message: message.text is not None)
async def handle_text(message: types.Message):
    prompt = message.text.strip()
    answer = "Ошибка при получении ответа от ИИ 🤖"
    try:
        completion = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        answer = completion.choices[0].message.content.strip()
        await message.answer(answer)
        await db.save_history(message.from_user.id, prompt, answer)
    except Exception as e:
        logging.exception(f"Ошибка при получении ответа от GPT: {e}")
        await message.answer(answer)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)









