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
from openai import AsyncOpenAI
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
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# SIMPLE USER SETTINGS (в будущем из БД)
user_voice_enabled = {}

# ==== DATABASE ====
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

    async def get_requests_today(self, user_id):
        async with self.pool.acquire() as connection:
            result = await connection.fetchval(
                "SELECT requests_today FROM users WHERE user_id = $1", user_id
            )
            return result or 0

    async def save_history(self, user_id, prompt, answer):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO usage_log (user_id, prompt, answer) VALUES ($1, $2, $3)",
                user_id, prompt, answer
            )

db = Database(DATABASE_URL)

# ==== FASTAPI EVENTS ====
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

# ==== COMMANDS ====
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer("Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Я твой AI-друг!\n\n"
        "/start — запуск\n"
        "/help — помощь\n"
        "/status — лимит\n"
        "/voice_on — включить ответы голосом\n"
        "/voice_off — только текст\n"
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id == OWNER_CHAT_ID:
        await message.answer("У тебя нет лимитов, мой хозяин! 👑")
        return
    count = await db.get_requests_today(message.from_user.id)
    await message.answer(f"Сегодня ты использовал {count}/3 запросов.")

@dp.message(Command("voice_on"))
async def cmd_voice_on(message: types.Message):
    user_voice_enabled[message.from_user.id] = True
    await message.answer("Теперь я буду озвучивать ответы голосом!")

@dp.message(Command("voice_off"))
async def cmd_voice_off(message: types.Message):
    user_voice_enabled[message.from_user.id] = False
    await message.answer("Озвучка отключена, буду только текстом.")

# ==== MAIN TEXT HANDLER ====
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id

    # Проверка лимита (кроме админа)
    if user_id != OWNER_CHAT_ID:
        count = await db.get_requests_today(user_id)
        if count >= 3:
            await message.answer("Превышен лимит запросов. Оформи подписку чтобы снимать лимит!")
            return
        await db.inc_requests(user_id)

    prompt = message.text.strip()
    answer = "Ошибка при получении ответа от ИИ 🤖"

    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=800,
            temperature=0.7
        )
        answer = resp.choices[0].message.content.strip()
        logging.info(f"OpenAI ответил: '{answer}'")
        await message.answer(answer)
    except Exception as e:
        logging.exception(e)
        await message.answer(answer)

    try:
        await db.save_history(user_id, prompt, answer)
    except Exception as e:
        logging.exception(e)

    # Озвучить, если включено
    if user_voice_enabled.get(user_id):
        try:
            audio_file = await tts_say(answer)
            await bot.send_voice(message.chat.id, audio_file)
        except Exception as e:
            logging.exception(e)
            await message.answer("Не получилось озвучить ответ 🤖")

# ==== VOICE HANDLER ====
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id

    # Проверка лимита (кроме админа)
    if user_id != OWNER_CHAT_ID:
        count = await db.get_requests_today(user_id)
        if count >= 3:
            await message.answer("Превышен лимит запросов. Оформи подписку чтобы снимать лимит!")
            return
        await db.inc_requests(user_id)

    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    local_path = f"/tmp/{file_id}.ogg"

    # Скачиваем голосовое
    await bot.download_file(file_path, local_path)

    text = ""
    answer = "Ошибка при получении ответа от ИИ 🤖"
    try:
        # Whisper Speech-to-Text
        async with aiofiles.open(local_path, "rb") as f:
            audio_bytes = await f.read()
        transcript = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_bytes,
            response_format="text"
        )
        text = transcript.strip()
        logging.info(f"Whisper распознал: '{text}'")
        if not text:
            await message.answer("Не смог распознать голос. Попробуй ещё раз!")
            return
        await message.answer(f"Ты сказал: <i>{text}</i>")
    except Exception as e:
        logging.exception(e)
        await message.answer("Не удалось распознать голосовое сообщение.")
        return

    # GPT-4o ответ на распознанный текст
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": text}
            ],
            max_tokens=800,
            temperature=0.7
        )
        answer = resp.choices[0].message.content.strip()
        await message.answer(answer)
    except Exception as e:
        logging.exception(e)
        await message.answer(answer)

    try:
        await db.save_history(user_id, text, answer)
    except Exception as e:
        logging.exception(e)

    # Озвучить, если включено
    if user_voice_enabled.get(user_id):
        try:
            audio_file = await tts_say(answer)
            await bot.send_voice(message.chat.id, audio_file)
        except Exception as e:
            logging.exception(e)
            await message.answer("Не получилось озвучить ответ 🤖")

# ==== TTS (Text-To-Speech) OpenAI ====
async def tts_say(text):
    import httpx
    import uuid
    filename = f"/tmp/{uuid.uuid4().hex}.mp3"
    client = httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
            json={
                "model": "tts-1",
                "input": text[:1000],  # Ограничение OpenAI
                "voice": "nova"
            }
        )
        response.raise_for_status()
        async with aiofiles.open(filename, "wb") as f:
            await f.write(response.content)
        return FSInputFile(filename)
    finally:
        await client.aclose()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)







