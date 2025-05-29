import os
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx
from serpapi import GoogleSearch
from databases import Database
from datetime import datetime
import hmac
import hashlib
from PIL import Image
import tempfile
import aiofiles

app = FastAPI()

BOT_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = "asst_uPuKSO4il3oJodGZUsLWH974"
SERPAPI_KEY = "292bb3653ec4db2e9abc418bc91548b1fec768997bf9f1aec3937f426272ae29"
CLOUDPAYMENTS_SECRET = os.getenv("CLOUDPAYMENTS_SECRET", "your_cloudpayments_secret_key")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bestfriend_db_user:Cm0DfEpdc2wvTPqrFd29ArMyJY4XYh5C@dpg-d0rmt7h5pdvs73a6h9m0-a/bestfriend_db")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = 520740282

client = OpenAI(api_key=OPENAI_API_KEY)
database = Database(DATABASE_URL)
usage_counter = {}
chat_histories = {}
started_users = set()

@app.on_event("startup")
async def startup():
    # connect to database
    await database.connect()
    # create tables if not exist
    await database.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    await database.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE,
            is_active BOOLEAN DEFAULT FALSE,
            expires_at TIMESTAMP,
            transaction_id TEXT,
            payment_method TEXT
        );
        """
    )
    await database.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_log (
            id SERIAL PRIMARY KEY,
            chat_id TEXT,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # set bot commands menu (without /date)
    await update_bot_commands()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

class TelegramMessage(BaseModel):
    update_id: int
    message: dict = None
    document: dict = None

async def send_message(chat_id, text):
    async with httpx.AsyncClient() as client_http:
        await client_http.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )

async def update_bot_commands():
    # define commands menu, excluding /date
    commands = [
        {"command": "start", "description": "Запуск бота"},
        {"command": "sub", "description": "Подписка"},
        {"command": "help", "description": "Инструкция"},
        {"command": "admin", "description": "Статистика (только для владельца)"}
    ]
    async with httpx.AsyncClient() as client_http:
        await client_http.post(
            f"{TELEGRAM_API}/setMyCommands",
            json={"commands": commands}
        )

@app.post("/webhook")
async def telegram_webhook(req: Request):
    body = await req.json()
    update = TelegramMessage(**body)

    if not update.message:
        return {"ok": True}

    try:
        msg = update.message
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()

        # register user and log usage
        await database.execute(
            "INSERT INTO users (chat_id) VALUES (:chat_id) ON CONFLICT (chat_id) DO NOTHING;",
            {"chat_id": str(chat_id)}
        )
        await database.execute(
            "INSERT INTO usage_log (chat_id) VALUES (:chat_id);",
            {"chat_id": str(chat_id)}
        )

        # setup or retrieve conversation thread
        if chat_id not in chat_histories:
            thread = client.beta.threads.create()
            chat_histories[chat_id] = {"thread_id": thread.id}
        else:
            thread = client.beta.threads.retrieve(chat_histories[chat_id]["thread_id"])

        # handle /start
        if text == "/start":
            if chat_id not in started_users:
                await send_message(chat_id, "👋 Привет! Я твой BESTFRIEND. Готов помочь! Просто напиши, что тебе нужно.")
                started_users.add(chat_id)
            return {"ok": True}

        # handle /sub
        if text == "/sub":
            await send_message(chat_id, "💳 Подписка: 399₽ в месяц или 2990₽ в год.\n\nПиши \"подписка\" для оформления.")
            return {"ok": True}

        # handle /help
        if text == "/help":
            await send_message(chat_id, "📖 Просто напиши, что хочешь: задай вопрос, попроси нарисовать изображение, уточни новости.")
            return {"ok": True}

        # handle /admin (owner only)
        if text == "/admin":
            if chat_id != OWNER_CHAT_ID:
                await send_message(chat_id, "⛔ У тебя нет доступа к этой команде.")
                return {"ok": True}
            total_users = await database.fetch_val("SELECT COUNT(*) FROM users;")
            total_requests = await database.fetch_val("SELECT COUNT(*) FROM usage_log;")
            active_subs = await database.fetch_val("SELECT COUNT(*) FROM subscriptions WHERE is_active = true;")
            message = (
                "📊 *Статистика:*\n"
                f"👥 Пользователей: {total_users}\n"
                f"📨 Запросов: {total_requests}\n"
                f"💳 Активных подписок: {active_subs}"
            )
            await send_message(chat_id, message)
            return {"ok": True}

        # handle image generation
        if any(kw in text.lower() for kw in ["нарисуй", "сгенерируй", "сделай картинку", "покажи изображение", "фото", "изображение"]):
            response = client.images.generate(
                model="dall-e-3",
                prompt=text,
                n=1,
                size="1024x1024"
            )
            image_url = response.data[0].url
            async with httpx.AsyncClient() as client_http:
                await client_http.post(
                    f"{TELEGRAM_API}/sendPhoto",
                    json={"chat_id": chat_id, "photo": image_url}
                )
            return {"ok": True}

        # handle news (SerpAPI)
        if "что нового" in text.lower() or "новости" in text.lower():
            params = {"q": "новости", "hl": "ru", "gl": "ru", "api_key": SERPAPI_KEY}
            search = GoogleSearch(params)
            results = search.get_dict()
            news_results = results.get("news_results", [])
            if not news_results:
                news = "Не удалось получить свежие новости."
            else:
                headlines = [f"• {item['title']}" for item in news_results[:5]]
                news = "\n".join(headlines)
            await send_message(chat_id, news)
            return {"ok": True}

        # handle regular queries via Assistants API
        client.beta.threads.messages.create(thread_id=thread.id, role="user", content=text)
        run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=ASSISTANT_ID)
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run_status.status == "completed":
                break
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        reply = messages.data[0].content[0].text.value
        await send_message(chat_id, reply)
        return {"ok": True}

    except Exception as e:
        await send_message(chat_id, f"⚠️ Ошибка: {str(e)}")

    return {"ok": True}




