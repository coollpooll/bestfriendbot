import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties  # ДОБАВИЛ ИМПОРТ!
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
from openai import OpenAI
from pydub import AudioSegment

load_dotenv()

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID"))

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))  # Исправлено тут!
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Пример Database класса (заглушка)
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

# ------- Голосовые сообщения через Whisper + pydub + GPT --------
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    ogg_path = f"voice_{message.from_user.id}.ogg"
    wav_path = f"voice_{message.from_user.id}.wav"
    await bot.download_file(file.file_path, ogg_path)
    # Переводим ogg в wav (формат, который понимает OpenAI)
    try:
        audio = AudioSegment.from_file(ogg_path, format="ogg")
        audio.export(wav_path, format="wav")
    except Exception as e:
        await message.answer("Не получилось обработать голосовое 😢")
        return
    # Отправляем в OpenAI на распознавание текста
    try:
        with open(wav_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                language="ru"
            )
        user_text = transcript.text if hasattr(transcript, "text") else str(transcript)
    except Exception as e:
        await message.answer("Ошибка при распознавании голосового 😔")
        return
    finally:
        # Удаляем временные файлы всегда
        try:
            os.remove(ogg_path)
            os.remove(wav_path)
        except Exception:
            pass
    # Отправляем распознанный текст в GPT и возвращаем ОТВЕТ
    try:
        gpt_response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": user_text}],
        )
        answer = gpt_response.choices[0].message.content
        await message.answer(answer)
    except Exception as e:
        await message.answer("Ошибка при получении ответа от ИИ 🤖")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)














