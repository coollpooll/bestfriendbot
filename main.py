import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
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

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# --- Database logic
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

    async def add_message(self, user_id, role, content):
        async with self.pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO dialog_history (user_id, role, content) VALUES ($1, $2, $3)",
                user_id, role, content
            )

    async def get_history(self, user_id, limit=16):
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT role, content FROM dialog_history
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id, limit
            )
            return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

db = Database(DATABASE_URL)

# --- Модные кнопки ПОМОЩЬ и ПОДПИСКА
from aiogram.types import BotCommand, ReplyKeyboardMarkup, KeyboardButton

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="help", description="Правила и описание бота"),
        BotCommand(command="sub", description="Оплата подписки"),
    ]
    await bot.set_my_commands(commands)

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="ПОМОЩЬ"), KeyboardButton(text="ПОДПИСКА")]
    ],
    resize_keyboard=True
)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.add_user(message.from_user.id)
    await message.answer(
        "Привет! Я твой BEST FRIEND 🤖\nГотов помочь с любыми вопросами!",
        reply_markup=main_keyboard
    )

@dp.message(F.text.lower() == "помощь")
@dp.message(F.text == "/help")
async def help_command(message: types.Message):
    help_text = (
        "<b>Правила пользования 🤖</b>\n"
        "1. Не спамь и не злоупотребляй ботом.\n"
        "2. Вопросы можно задавать голосом или текстом.\n"
        "3. Все сообщения обрабатывает нейросеть GPT-4o с памятью (контекст сохраняется).\n\n"
        "<b>Модель:</b> GPT-4o — умная, быстрая, понимает русский, учитывает весь твой диалог.\n"
        "<b>Для расширенного доступа — оформи подписку через ПОДПИСКА.</b>\n"
    )
    await message.answer(help_text)

@dp.message(F.text.lower() == "подписка")
@dp.message(F.text == "/sub")
async def sub_command(message: types.Message):
    sub_url = "https://your-payment-link.com"  # ← потом сюда реальную ссылку
    await message.answer(
        "🔗 <b>Оплатить подписку</b>\n\nПерейди по ссылке:\n" + sub_url,
        disable_web_page_preview=True
    )

@app.on_event("startup")
async def on_startup():
    await db.connect()
    logging.info("Database connected")
    await set_bot_commands(bot)

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

# ------- Голосовые сообщения через Whisper + pydub + GPT-4o --------
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    ogg_path = f"voice_{user_id}.ogg"
    wav_path = f"voice_{user_id}.wav"
    await bot.download_file(file.file_path, ogg_path)
    try:
        audio = AudioSegment.from_file(ogg_path, format="ogg")
        audio.export(wav_path, format="wav")
    except Exception:
        await message.answer("Не получилось обработать голосовое 😢")
        return
    try:
        with open(wav_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                language="ru"
            )
        user_text = transcript.text if hasattr(transcript, "text") else str(transcript)
    except Exception:
        await message.answer("Ошибка при распознавании голосового 😔")
        return
    finally:
        try:
            os.remove(ogg_path)
            os.remove(wav_path)
        except Exception:
            pass
    await db.add_message(user_id, "user", user_text)
    history = await db.get_history(user_id, limit=16)
    try:
        gpt_response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=history,
        )
        answer = gpt_response.choices[0].message.content
        await db.add_message(user_id, "assistant", answer)
        await message.answer(answer)
    except Exception:
        await message.answer("Ошибка при получении ответа от ИИ 🤖")

# ------- Текстовые сообщения (GPT-4o + память) --------
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    if user_text.lower() in ["помощь", "подписка", "/help", "/sub"]:
        return  # уже обработано выше

    await db.add_message(user_id, "user", user_text)
    history = await db.get_history(user_id, limit=16)
    try:
        gpt_response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=history,
        )
        answer = gpt_response.choices[0].message.content
        await db.add_message(user_id, "assistant", answer)
        await message.answer(answer)
    except Exception:
        await message.answer("Ошибка при получении ответа от ИИ 🤖")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)

















