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
import tempfile

load_dotenv()

logging.basicConfig(level=logging.INFO)

# ENV vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID"))  # твой Telegram user_id

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# База данных
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
        if user_id == OWNER_CHAT_ID:
            return
        async with self.pool.acquire() as connection:
            await connection.execute(
                "UPDATE users SET requests_today = requests_today + 1 WHERE user_id = $1", user_id
            )

    async def get_requests(self, user_id):
        if user_id == OWNER_CHAT_ID:
            return 0
        async with self.pool.acquire() as connection:
            result = await connection.fetchrow(
                "SELECT requests_today FROM users WHERE user_id = $1", user_id
            )
            if result:
                return result['requests_today']
            else:
                return 0

db = Database(DATABASE_URL)

# Команды
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer(
        "Привет! Я твой BEST FRIEND 🤖\n\n"
        "Готов помочь с любыми вопросами!\n"
        "Поддерживаю текст, голосовые и картинки. /help для справки."
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Я — бот на GPT-4o!\n\n"
        "Могу:\n"
        "— Ответить на текст (GPT-4o)\n"
        "— Ответить на голосовое (Whisper + GPT)\n"
        "— Создать картинку по описанию (напиши /img ...)\n"
        "— Лимит: 3 запроса/день бесплатно. Подписка = безлимит.\n"
        "/status — узнать остаток лимита."
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    count = await db.get_requests(message.from_user.id)
    if message.from_user.id == OWNER_CHAT_ID:
        await message.answer("🔥 Для тебя, босс, лимитов нет!")
    else:
        await message.answer(f"Сегодня использовано запросов: {count}/3")

# Команда — создать картинку
@dp.message(Command("img"))
async def create_image(message: types.Message):
    user_id = message.from_user.id
    requests_today = await db.get_requests(user_id)
    if user_id != OWNER_CHAT_ID and requests_today >= 3:
        await message.answer("⛔ Лимит запросов на сегодня исчерпан!\nОформи подписку для безлимита.")
        return
    await db.inc_requests(user_id)
    prompt = message.text.replace("/img", "").strip()
    if not prompt:
        await message.answer("❗️Опиши, что нарисовать. Пример: /img желтая балаклава среди небоскребов")
        return
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        result = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        url = result.data[0].url
        await message.answer_photo(url, caption="Готово! Сгенерировано DALL-E 3")
    except Exception as e:
        logging.error(f"Image gen error: {e}")
        await message.answer("🤖 Ошибка генерации изображения.")

# Голосовые сообщения
@dp.message(lambda msg: msg.voice)
async def voice_message_handler(message: types.Message):
    user_id = message.from_user.id
    requests_today = await db.get_requests(user_id)
    if user_id != OWNER_CHAT_ID and requests_today >= 3:
        await message.answer("⛔ Лимит запросов на сегодня исчерпан!\nОформи подписку для безлимита.")
        return
    await db.inc_requests(user_id)
    file_info = await bot.get_file(message.voice.file_id)
    file = await bot.download_file(file_info.file_path)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_ogg:
        temp_ogg.write(file.read())
        temp_ogg.flush()
        temp_wav = temp_ogg.name.replace(".ogg", ".wav")
        # Конвертация ogg → wav
        AudioSegment.from_ogg(temp_ogg.name).export(temp_wav, format="wav")
        # Распознавание речи (Whisper)
        recognizer = sr.Recognizer()
        with sr.AudioFile(temp_wav) as source:
            audio = recognizer.record(source)
            try:
                text = recognizer.recognize_google(audio, language="ru-RU")
            except Exception as e:
                logging.error(f"Voice recognize error: {e}")
                await message.answer("🤖 Не удалось распознать голосовое.")
                return
    # GPT-4o ответ на распознанный текст
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты дружелюбный ассистент."},
                {"role": "user", "content": text}
            ]
        )
        reply = response.choices[0].message.content
        await message.answer(f"<b>Распознано:</b> {text}\n\n<b>GPT:</b> {reply}")
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        await message.answer("🤖 Готово! Но что-то пошло не так с получением ответа от GPT.")

# Любое текстовое сообщение (кроме команд)
@dp.message(lambda msg: not msg.text.startswith('/'))
async def gpt4o_reply(message: types.Message):
    user_id = message.from_user.id
    requests_today = await db.get_requests(user_id)
    if user_id != OWNER_CHAT_ID and requests_today >= 3:
        await message.answer("⛔ Лимит запросов на сегодня исчерпан!\nОформи подписку для безлимита.")
        return
    await db.inc_requests(user_id)
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

# FastAPI endpoints
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




