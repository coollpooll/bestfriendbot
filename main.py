import os
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx
from serpapi import GoogleSearch
from databases import Database
from datetime import datetime, timezone
import hmac
import hashlib

app = FastAPI()

BOT_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = "asst_uPuKSO4il3oJodGZUsLWH974"
SERPAPI_KEY = "292bb3653ec4db2e9abc418bc91548b1fec768997bf9f1aec3937f426272ae29"
CLOUDPAYMENTS_SECRET = os.getenv("CLOUDPAYMENTS_SECRET", "your_cloudpayments_secret_key")
DATABASE_URL = "postgresql://bestfriend_db_user:Cm0DfEpdc2wvTPqrFd29ArMyJY4XYh5C@dpg-d0rmt7h5pdvs73a6h9m0-a/bestfriend_db"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

client = OpenAI(api_key=OPENAI_API_KEY)
database = Database(DATABASE_URL)
usage_counter = {}
chat_histories = {}

@app.on_event("startup")
async def startup():
    await database.connect()
    await database.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    await database.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE,
            is_active BOOLEAN DEFAULT FALSE,
            expires_at TIMESTAMP,
            transaction_id TEXT,
            payment_method TEXT
        );
    """)
    await database.execute("""
        CREATE TABLE IF NOT EXISTS usage_log (
            id SERIAL PRIMARY KEY,
            chat_id TEXT,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

class TelegramMessage(BaseModel):
    update_id: int
    message: dict = None

async def send_message(chat_id, text):
    async with httpx.AsyncClient() as client_http:
        await client_http.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })

async def update_bot_commands():
    commands = [
        {"command": "start", "description": "Запуск бота"},
        {"command": "sub", "description": "Подписка"},
        {"command": "help", "description": "Инструкция"},
        {"command": "admin", "description": "Статистика (только для владельца)"}
    ]
    async with httpx.AsyncClient() as client_http:
        await client_http.post(f"{TELEGRAM_API}/setMyCommands", json={"commands": commands})

def get_latest_news():
    params = {
        "q": "новости",
        "hl": "ru",
        "gl": "ru",
        "api_key": SERPAPI_KEY
    }
    search = GoogleSearch(params)
    results = search.get_dict()
    news_results = results.get("news_results", [])
    if not news_results:
        return "Не удалось получить свежие новости."
    headlines = [f"• {item['title']}" for item in news_results[:5]]
    return "\n".join(headlines)

async def generate_dalle(prompt):
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        n=1,
        size="1024x1024"
    )
    return response.data[0].url

async def check_subscription(chat_id):
    row = await database.fetch_one("""
        SELECT is_active, expires_at FROM subscriptions WHERE chat_id = :chat_id
    """, {"chat_id": str(chat_id)})
    if row and row["is_active"] and row["expires_at"]:
        return row["expires_at"] > datetime.now(timezone.utc)
    return False

@app.post("/cloudpayments")
async def cloudpayments_webhook(request: Request):
    data = await request.json()
    provided_signature = request.headers.get("Content-HMAC")
    calculated_signature = hmac.new(CLOUDPAYMENTS_SECRET.encode(), request.body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(provided_signature, calculated_signature):
        return {"code": 13, "message": "Invalid signature"}

    chat_id = data.get("AccountId")
    transaction_id = data.get("TransactionId")
    payment_method = data.get("PaymentMethod")
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)

    await database.execute("""
        INSERT INTO subscriptions (chat_id, is_active, expires_at, transaction_id, payment_method)
        VALUES (:chat_id, true, :expires, :transaction_id, :payment_method)
        ON CONFLICT (chat_id) DO UPDATE
        SET is_active = true, expires_at = :expires, transaction_id = :transaction_id, payment_method = :payment_method;
    """, {
        "chat_id": chat_id,
        "expires": expires,
        "transaction_id": transaction_id,
        "payment_method": payment_method
    })

    return {"code": 0, "message": "OK"}

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

        await database.execute("""
            INSERT INTO users (chat_id) VALUES (:chat_id)
            ON CONFLICT (chat_id) DO NOTHING;
        """, {"chat_id": str(chat_id)})

        if text.startswith("/start"):
            await update_bot_commands()
            await send_message(chat_id,
                """👋 Привет, я BEST FRIEND 🤖 — я твой личный ИИ, который не ищет в тебе выгоду, не уговаривает, не льстит.

🎓 Заменяю любые платные курсы.
🧠 Отвечаю как GPT-4.
🎨 Рисую картинки.
🎥 Скоро — видео.

🆓 3 запроса каждый день — бесплатно.
💳 Подписка: 399₽/мес или 2990₽/год.

Начни с любого запроса. Я уже жду."""
            )
            return {"ok": True}

        if text.startswith("/sub"):
            await send_message(chat_id,
                "💳 Подписка: 399₽ в месяц или 2990₽ в год.\n\nПиши \"подписка\" или нажми кнопку (в разработке), чтобы оформить."
            )
            return {"ok": True}

        if text.startswith("/help"):
            await send_message(chat_id,
                "📖 Просто напиши, что хочешь: задай вопрос, попроси нарисовать изображение, уточни новости.\n\nЯ всё пойму!"
            )
            return {"ok": True}

        if text.startswith("/admin") and str(chat_id) == "520740282":
            user_count = await database.fetch_val("SELECT COUNT(*) FROM users")
            subs_count = await database.fetch_val("SELECT COUNT(*) FROM subscriptions WHERE is_active = true")
            usage_count = await database.fetch_val("SELECT COUNT(*) FROM usage_log")
            await send_message(chat_id, f"👤 Пользователей: {user_count}\n💳 Подписок: {subs_count}\n📊 Запросов: {usage_count}")
            return {"ok": True}

        user_id = str(chat_id)
        is_owner = user_id == "520740282"
        is_subscribed = await check_subscription(user_id)

        if not is_owner and not is_subscribed:
            usage_key = f"user_usage:{user_id}"
            count = usage_counter.get(usage_key, 0)
            if count >= 3:
                await send_message(chat_id, "❌ Лимит исчерпан. 3 запроса в день бесплатно.\n\nОформи подписку за 399₽ и пользуйся без ограничений.")
                return {"ok": True}
            usage_counter[usage_key] = count + 1

        await database.execute("""
            INSERT INTO usage_log (chat_id) VALUES (:chat_id)
        """, {"chat_id": user_id})

        if any(kw in text.lower() for kw in ["нарисуй", "сгенерируй", "сделай картинку", "покажи изображение", "фото", "изображение"]):
            image_url = await generate_dalle(text)
            async with httpx.AsyncClient() as client_http:
                await client_http.post(f"{TELEGRAM_API}/sendPhoto", json={"chat_id": chat_id, "photo": image_url})
            return {"ok": True}

        if "что нового" in text.lower() or "новости" in text.lower():
            news = get_latest_news()
            await send_message(chat_id, news)
            return {"ok": True}

        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=text
        )

        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID
        )

        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run_status.status == "completed":
                break

        messages = client.beta.threads.messages.list(thread_id=thread.id)
        reply = messages.data[0].content[0].text.value
        await send_message(chat_id, reply)

    except Exception as e:
        await send_message(chat_id, f"⚠️ Ошибка: {str(e)}")

    return {"ok": True}













