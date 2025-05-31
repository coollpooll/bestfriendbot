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
from aiogram.types import BotCommand, ReplyKeyboardMarkup, KeyboardButton
from PIL import Image
import io

# Импорты для документов
import mimetypes
import zipfile
import rarfile

from PyPDF2 import PdfReader
from docx import Document as DocxDocument
import csv
import pandas as pd
import pptx
import xlrd
import openpyxl
import textract

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

# --- Database logic (без изменений)
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

# --- Кнопки
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
        "2. Вопросы можно задавать голосом, текстом, картинкой или документом (PDF, Word, Excel, PPTX, ZIP, RAR, TXT, CSV и др.).\n"
        "3. Все сообщения обрабатывает нейросеть GPT-4o (есть память и поддержка картинок/файлов).\n\n"
        "<b>Модель:</b> GPT-4o — умная, быстрая, понимает русский, учитывает весь твой диалог.\n"
        "<b>Генерируй картинки текстом! Просто напиши 'сгенерируй картинку: [описание]'</b>\n"
        "<b>Для расширенного доступа — оформи подписку через ПОДПИСКА.</b>\n"
    )
    await message.answer(help_text)

@dp.message(F.text.lower() == "подписка")
@dp.message(F.text == "/sub")
async def sub_command(message: types.Message):
    sub_url = "https://your-payment-link.com"
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

# -------- Генерация изображений --------
@dp.message(F.text.lower().startswith("сгенерируй картинку"))
async def generate_image(message: types.Message):
    prompt = message.text.partition(":")[2].strip()
    if not prompt:
        await message.answer("Напиши описание картинки после 'сгенерируй картинку:'")
        return
    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url
        await message.answer_photo(image_url, caption="Готово! Если хочешь ещё — просто напиши новый запрос.")
    except Exception as e:
        await message.answer("Ошибка при генерации картинки 😔")

# ------- Голосовые сообщения (Whisper + GPT-4o) --------
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

# ------- Распознавание изображений (GPT-4o Vision) --------
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    img_bytes = await bot.download_file(file.file_path)
    image_data = img_bytes.read()
    # Отправить картинку и подпись (если есть) в GPT-4o Vision
    gpt_messages = [{"role": "user", "content": [{"type": "text", "text": message.caption or "Что на фото?"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_data.hex()}}]}]
    try:
        gpt_response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=gpt_messages
        )
        answer = gpt_response.choices[0].message.content
        await message.answer(answer)
    except Exception:
        await message.answer("Ошибка при распознавании изображения 😔")

# ------- Распознавание документов (мультитригер) --------
@dp.message(F.document)
async def handle_document(message: types.Message):
    user_id = message.from_user.id
    document = message.document
    file = await bot.get_file(document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    filename = document.file_name.lower()

    content = None
    format_note = ""

    # DOCX, DOC
    if filename.endswith(".docx"):
        doc = DocxDocument(io.BytesIO(file_bytes.read()))
        content = "\n".join([p.text for p in doc.paragraphs])
        format_note = "Word DOCX:"
    elif filename.endswith(".doc"):
        try:
            content = textract.process(io.BytesIO(file_bytes.read())).decode('utf-8')
            format_note = "Word DOC:"
        except Exception:
            content = "Не удалось прочитать DOC-файл."
    # PDF
    elif filename.endswith(".pdf"):
        pdf = PdfReader(io.BytesIO(file_bytes.read()))
        text = ""
        for page in pdf.pages[:5]:
            text += page.extract_text() or ""
        content = text
        format_note = "PDF:"
    # TXT
    elif filename.endswith(".txt"):
        content = file_bytes.read().decode("utf-8", errors="ignore")
        format_note = "TXT:"
    # CSV
    elif filename.endswith(".csv"):
        file_bytes.seek(0)
        df = pd.read_csv(file_bytes, delimiter=',', nrows=100)
        content = df.head(20).to_string()
        format_note = "CSV (таблица):"
    # XLSX
    elif filename.endswith(".xlsx"):
        wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes.read()), read_only=True)
        ws = wb.active
        data = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 200: break
            data.append("\t".join([str(cell) if cell is not None else "" for cell in row]))
        content = "\n".join(data)
        format_note = "XLSX:"
    # XLS
    elif filename.endswith(".xls"):
        book = xlrd.open_workbook(file_contents=file_bytes.read())
        sheet = book.sheet_by_index(0)
        rows = []
        for i in range(min(200, sheet.nrows)):
            rows.append("\t".join([str(cell.value) for cell in sheet.row(i)]))
        content = "\n".join(rows)
        format_note = "XLS:"
    # PPTX
    elif filename.endswith(".pptx"):
        prs = pptx.Presentation(io.BytesIO(file_bytes.read()))
        slides_text = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    slides_text.append(shape.text)
        content = "\n".join(slides_text)
        format_note = "PPTX (презентация):"
    # RTF
    elif filename.endswith(".rtf"):
        content = textract.process(io.BytesIO(file_bytes.read())).decode('utf-8')
        format_note = "RTF:"
    # ZIP
    elif filename.endswith(".zip"):
        file_bytes.seek(0)
        with zipfile.ZipFile(file_bytes, "r") as zipf:
            file_list = zipf.namelist()
            content = "В архиве ZIP следующие файлы:\n" + "\n".join(file_list[:20])
            format_note = "ZIP-архив:"
    # RAR
    elif filename.endswith(".rar"):
        file_bytes.seek(0)
        with rarfile.RarFile(fileobj=io.BytesIO(file_bytes.read())) as rar:
            file_list = rar.namelist()
            content = "В архиве RAR следующие файлы:\n" + "\n".join(file_list[:20])
            format_note = "RAR-архив:"
    # Картинки
    elif filename.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")):
        # По сути как обычное фото
        await handle_photo(message)
        return
    else:
        # Попробовать просто извлечь текст
        try:
            content = textract.process(io.BytesIO(file_bytes.read())).decode('utf-8')
            format_note = "Другой формат (textract):"
        except Exception:
            content = "Не удалось извлечь текст из файла."
            format_note = "Формат поддерживается частично:"

    prompt = f"{format_note}\n{content[:4000]}"
    await db.add_message(user_id, "user", prompt)
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
        await message.answer("Ошибка при обработке файла 🤖")

# ------- Текстовые сообщения (GPT-4o + память) --------
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    if user_text.lower() in ["помощь", "подписка", "/help", "/sub"] or user_text.lower().startswith("сгенерируй картинку"):
        return

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


















