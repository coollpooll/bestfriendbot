import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, CommandStart
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
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "520740282"))

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# --- База памяти для TTS (озвучки)
tts_enabled = {}

# --- Пример Database класса (заглушка)
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
    await message.answer("Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!\n\nДоступные команды:\n/help — помощь\n/status — лимиты\n/tts_on — включить озвучку (только админ)\n/tts_off — выключить озвучку (только админ)")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("Список команд:\n/start — перезапуск\n/status — лимиты\n/tts_on — включить озвучку (только админ)\n/tts_off — выключить озвучку (только админ)")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer("Лимиты: 3 запроса в сутки (если не админ).")

@dp.message(Command("tts_on"))
async def cmd_tts_on(message: types.Message):
    if message.from_user.id == OWNER_CHAT_ID:
        tts_enabled[OWNER_CHAT_ID] = True
        await message.answer("Озвучка включена.")
    else:
        await message.answer("У тебя нет доступа к этой команде.")

@dp.message(Command("tts_off"))
async def cmd_tts_off(message: types.Message):
    if message.from_user.id == OWNER_CHAT_ID:
        tts_enabled[OWNER_CHAT_ID] = False
        await message.answer("Озвучка выключена.")
    else:
        await message.answer("У тебя нет доступа к этой команде.")

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

# ------- Текстовые сообщения --------
@dp.message(F.text)
async def handle_text(message: types.Message):
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты дружелюбный помощник. Отвечай коротко и по делу."},
                {"role": "user", "content": message.text}
            ]
        )
        gpt_answer = completion.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка при получении ответа от GPT: {e}")
        await message.answer("Ошибка при получении ответа от ИИ 🤖")
        return

    await message.answer(gpt_answer)

    # Озвучка (если включена для админа)
    if tts_enabled.get(OWNER_CHAT_ID) and message.from_user.id == OWNER_CHAT_ID:
        await send_tts_voice(message, gpt_answer)

# ------- Голосовые сообщения через Whisper + pydub --------
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
    # Отправляем в OpenAI Whisper
    try:
        with open(wav_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                language="ru"
            )
        prompt = transcript.text if hasattr(transcript, "text") else str(transcript)
    except Exception as e:
        logging.error(f"Ошибка при распознавании голоса: {e}")
        await message.answer("Ошибка при распознавании голосового 😔")
        return
    finally:
        # Удаляем временные файлы
        try:
            os.remove(ogg_path)
            os.remove(wav_path)
        except Exception:
            pass
    # Отправляем в GPT-4o
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты дружелюбный помощник. Отвечай коротко и по делу."},
                {"role": "user", "content": prompt}
            ]
        )
        gpt_answer = completion.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка при получении ответа от GPT: {e}")
        await message.answer("Ошибка при получении ответа от ИИ 🤖")
        return

    await message.answer(gpt_answer)

    # Озвучка (если включена для админа)
    if tts_enabled.get(OWNER_CHAT_ID) and message.from_user.id == OWNER_CHAT_ID:
        await send_tts_voice(message, gpt_answer)

# ---------- TTS (Text-to-Speech) ----------
async def send_tts_voice(message, text):
    try:
        # Ограничение длины (OpenAI TTS max ~4096)
        text = text[:4096]
        speech_response = openai_client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text,
        )
        filename = f"tts_{message.from_user.id}.mp3"
        with open(filename, "wb") as f:
            f.write(speech_response.content)
        with open(filename, "rb") as f:
            await bot.send_voice(message.chat.id, f)
        os.remove(filename)
    except Exception as e:
        logging.error(f"TTS ошибка: {e}")
        await message.answer("Ошибка озвучки ответа.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)













