import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
from openai import OpenAI
import httpx
import aiofiles

load_dotenv()

logging.basicConfig(level=logging.INFO)

# ENV VARS
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", 0))

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# DB helper
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

    async def get_requests_today(self, user_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT requests_today FROM users WHERE user_id = $1", user_id)
            return row["requests_today"] if row else 0

    async def increment_requests(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET requests_today = requests_today + 1 WHERE user_id = $1", user_id)

    async def reset_requests(self):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET requests_today = 0")

    async def save_history(self, user_id, user_message, ai_response):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO usage_log (user_id, user_message, ai_response) VALUES ($1, $2, $3)",
                user_id, user_message, ai_response
            )

db = Database(DATABASE_URL)

# OpenAI
openai = OpenAI(api_key=OPENAI_API_KEY)

# Simple user settings in RAM (for /voiceon)
user_voice_mode = set()

# START
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer("Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!\n\nДоступные команды:\n/start — приветствие\n/help — помощь\n/status — лимит\n/voiceon — отвечать голосом\n/voiceoff — выключить озвучку")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("Спроси меня что угодно. Я умею:\n— отвечать на вопросы (GPT-4o)\n— распознавать голосовые (Whisper)\n— озвучивать ответы (OpenAI TTS)\n— генерировать картинки\n\nДоступные команды:\n/status — лимит\n/voiceon — включить голосовые ответы\n/voiceoff — выключить озвучку")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    requests = await db.get_requests_today(message.from_user.id)
    await message.answer(f"Ты использовал {requests} из 3 бесплатных запросов сегодня.")

@dp.message(Command("voiceon"))
async def cmd_voiceon(message: types.Message):
    user_voice_mode.add(message.from_user.id)
    await message.answer("Теперь я буду отвечать тебе голосом (озвучивать ответы) 🎤")

@dp.message(Command("voiceoff"))
async def cmd_voiceoff(message: types.Message):
    user_voice_mode.discard(message.from_user.id)
    await message.answer("Голосовой режим выключен.")

# Вспомогательная функция: лимиты
async def can_ask(user_id):
    if user_id == OWNER_CHAT_ID:
        return True
    return (await db.get_requests_today(user_id)) < 3

async def increment_limit(user_id):
    if user_id != OWNER_CHAT_ID:
        await db.increment_requests(user_id)

# Обработка текстовых сообщений
@dp.message(F.text)
async def handle_text(message: types.Message):
    if not await can_ask(message.from_user.id):
        await message.answer("Лимит 3 запроса в сутки. Чтобы снять лимит, оформи подписку 😉")
        return
    prompt = message.text
    try:
        completion = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1200,
        )
        answer = completion.choices[0].message.content
        await db.save_history(message.from_user.id, prompt, answer)
        await message.answer(answer)
        # Озвучка если включена
        if message.from_user.id == OWNER_CHAT_ID or message.from_user.id in user_voice_mode:
            audio_file = await tts_say(answer)
            await bot.send_voice(message.chat.id, audio_file)
        await increment_limit(message.from_user.id)
    except Exception as e:
        logging.exception(e)
        await message.answer("Ошибка при получении ответа от ИИ 🤖")

# Обработка голосовых сообщений
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    if not await can_ask(message.from_user.id):
        await message.answer("Лимит 3 запроса в сутки. Чтобы снять лимит, оформи подписку 😉")
        return
    # Скачиваем файл
    file_info = await bot.get_file(message.voice.file_id)
    voice_path = f"voice_{message.voice.file_id}.ogg"
    await bot.download_file(file_info.file_path, voice_path)
    # Распознаём через Whisper
    with open(voice_path, "rb") as f:
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
            language="ru"
        )
    prompt = transcript
    try:
        completion = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1200,
        )
        answer = completion.choices[0].message.content
        await db.save_history(message.from_user.id, prompt, answer)
        await message.answer(answer)
        # Озвучка если включена
        if message.from_user.id == OWNER_CHAT_ID or message.from_user.id in user_voice_mode:
            audio_file = await tts_say(answer)
            await bot.send_voice(message.chat.id, audio_file)
        await increment_limit(message.from_user.id)
    except Exception as e:
        logging.exception(e)
        await message.answer("Ошибка при получении ответа от ИИ 🤖")
    finally:
        try:
            os.remove(voice_path)
        except: pass

# Озвучка OpenAI TTS
async def tts_say(text):
    response = openai.audio.speech.create(
        model="tts-1-hd",
        input=text,
        voice="nova"
    )
    mp3_bytes = response.content
    filename = "tts_reply.mp3"
    async with aiofiles.open(filename, 'wb') as f:
        await f.write(mp3_bytes)
    return FSInputFile(filename)

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





