import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from dotenv import load_dotenv
import asyncpg
from fastapi import FastAPI, Request
from openai import AsyncOpenAI
import aiofiles

load_dotenv()

# Логирование для отладки
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "520740282"))

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

USER_LIMIT = 3  # лимит обычным юзерам

# Simple dict to keep tts state (for demo, use DB for production)
tts_states = {}

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

    async def get_requests_today(self, user_id):
        async with self.pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT requests_today FROM users WHERE user_id=$1", user_id
            )
            return result or 0

    async def increment_requests(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET requests_today = requests_today + 1 WHERE user_id=$1",
                user_id
            )

    async def reset_all_limits(self):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET requests_today=0")

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
    await message.answer(
        "Привет! Я твой BEST FRIEND 🤖\n\n"
        "Готов помочь с любыми вопросами. Тестовый лимит: 3 запроса в сутки.\n"
        "Включить озвучку ответов: /tts_on\n"
        "Выключить озвучку: /tts_off"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Бот поддерживает:\n"
        "- Текстовые и голосовые сообщения\n"
        "- Генерацию картинок (напиши /image и промпт)\n"
        "- Озвучку ответов (вкл/выкл: /tts_on /tts_off)\n"
        "- Лимит 3 запроса в день (кроме админа)\n"
        "/status — посмотреть лимит"
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id == OWNER_CHAT_ID:
        await message.answer("Для тебя лимитов нет, мой повелитель.")
        return
    count = await db.get_requests_today(message.from_user.id)
    await message.answer(f"Сегодня ты использовал {count} из {USER_LIMIT} запросов.")

@dp.message(Command("tts_on"))
async def cmd_tts_on(message: types.Message):
    tts_states[message.from_user.id] = True
    await message.answer("Озвучка включена. Теперь я буду отвечать голосом и текстом!")

@dp.message(Command("tts_off"))
async def cmd_tts_off(message: types.Message):
    tts_states[message.from_user.id] = False
    await message.answer("Озвучка отключена. Теперь ответы будут только текстом.")

async def check_limit(user_id):
    if user_id == OWNER_CHAT_ID:
        return True
    requests = await db.get_requests_today(user_id)
    return requests < USER_LIMIT

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    if not await check_limit(message.from_user.id):
        await message.answer("Ты исчерпал свой лимит запросов на сегодня! Оформи подписку для снятия лимитов 😉")
        return
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    local_path = f"voice_{message.from_user.id}.ogg"
    await bot.download_file(file_path, local_path)
    # Преобразуем в формат ogg/opus (если нужно — используем ffmpeg для m4a/wav и т.д.)
    async with aiofiles.open(local_path, "rb") as f:
        data = await f.read()
    try:
        transcript = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=data,
            response_format="text",
            language="ru"
        )
        prompt = transcript
    except Exception as e:
        logging.error(f"Ошибка при распознавании голоса: {e}")
        await message.answer("Ошибка при распознавании голосового 😔")
        return
    try:
        completion = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        answer = completion.choices[0].message.content.strip()
        await db.increment_requests(message.from_user.id)
        await db.save_history(message.from_user.id, prompt, answer)
    except Exception as e:
        logging.error(f"Ошибка при ответе GPT: {e}")
        await message.answer("Ошибка при получении ответа от ИИ 🤖")
        return
    await message.answer(answer)
    # Озвучка если включена
    if tts_states.get(message.from_user.id, False):
        try:
            speech = await openai_client.audio.speech.create(
                model="tts-1",
                input=answer,
                voice="nova",  # Можно выбрать: nova, alloy, echo, fable, onyx, shimmer
                response_format="ogg_opus"
            )
            audio_file = f"tts_{message.from_user.id}.ogg"
            async with aiofiles.open(audio_file, "wb") as out_f:
                await out_f.write(await speech.aread())
            await bot.send_voice(message.chat.id, types.FSInputFile(audio_file))
        except Exception as e:
            logging.error(f"Ошибка озвучки: {e}")
            await message.answer("Ошибка озвучки ответа 😢")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text.startswith("/image"):
        # Картинки только через DALL-E, позже добавишь SD
        prompt = message.text.replace("/image", "").strip()
        if not prompt:
            await message.answer("Напиши описание картинки после /image")
            return
        try:
            image_resp = await openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                n=1
            )
            image_url = image_resp.data[0].url
            await message.answer_photo(image_url, caption="Вот твоя картинка 👇")
        except Exception as e:
            logging.error(f"Ошибка генерации картинки: {e}")
            await message.answer("Ошибка при генерации картинки 😔")
        return

    if not await check_limit(message.from_user.id):
        await message.answer("Ты исчерпал свой лимит запросов на сегодня! Оформи подписку для снятия лимитов 😉")
        return
    prompt = message.text
    try:
        completion = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        answer = completion.choices[0].message.content.strip()
        await db.increment_requests(message.from_user.id)
        await db.save_history(message.from_user.id, prompt, answer)
    except Exception as e:
        logging.error(f"Ошибка при получении ответа от GPT: {e}")
        await message.answer("Ошибка при получении ответа от ИИ 🤖")
        return
    await message.answer(answer)
    # Озвучка если включена
    if tts_states.get(message.from_user.id, False):
        try:
            speech = await openai_client.audio.speech.create(
                model="tts-1",
                input=answer,
                voice="nova",
                response_format="ogg_opus"
            )
            audio_file = f"tts_{message.from_user.id}.ogg"
            async with aiofiles.open(audio_file, "wb") as out_f:
                await out_f.write(await speech.aread())
            await bot.send_voice(message.chat.id, types.FSInputFile(audio_file))
        except Exception as e:
            logging.error(f"Ошибка озвучки: {e}")
            await message.answer("Ошибка озвучки ответа 😢")

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










