import os
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx
from serpapi import GoogleSearch
from databases import Database
import aiofiles
import PyPDF2

app = FastAPI()

# Configuration from environment
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID", "asst_uPuKSO4il3oJodGZUsLWH974")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "your_serpapi_key")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "520740282"))
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

client = OpenAI(api_key=OPENAI_API_KEY)
database = Database(DATABASE_URL) if DATABASE_URL else None
chat_histories: dict[int, str] = {}
started_users: set[int] = set()

@app.on_event("startup")
async def startup():
    if database:
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
    await update_bot_commands()

@app.on_event("shutdown")
async def shutdown():
    if database:
        await database.disconnect()

class TelegramMessage(BaseModel):
    update_id: int
    message: dict | None = None

async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient() as client_http:
        try:
            await client_http.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            )
        except Exception as e:
            print(f"Failed to send message: {e}")

async def download_telegram_file(file_id: str, dest_path: str) -> None:
    async with httpx.AsyncClient() as client_http:
        r = await client_http.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
        result = r.json().get("result", {})
        file_path = result.get("file_path")
        if not file_path:
            return
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        resp = await client_http.get(file_url)
        async with aiofiles.open(dest_path, 'wb') as f:
            await f.write(resp.content)

async def update_bot_commands():
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
    update = TelegramMessage(**(await req.json()))
    if not update.message:
        return {"ok": True}

    msg = update.message
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip() if msg.get("text") else ""

    # File or document upload
    if doc := msg.get("document"):
        file_id = doc.get("file_id")
        file_name = doc.get("file_name", "file")
        dest = f"/tmp/{file_id}_{file_name}"
        await download_telegram_file(file_id, dest)
        await send_message(chat_id, f"✅ Файл *{file_name}* получен и сохранён.")
        ext = file_name.lower().split('.')[-1]
        if ext in ('pdf', 'txt'):
            text_content = ''
            if ext == 'pdf':
                try:
                    reader = PyPDF2.PdfReader(dest)
                    for page in reader.pages:
                        text_content += (page.extract_text() or '') + '\n'
                except Exception as e:
                    await send_message(chat_id, f"Ошибка при чтении PDF: {e}")
                    return {"ok": True}
            else:
                async with aiofiles.open(dest, 'r', encoding='utf-8') as f:
                    text_content = await f.read()
            snippet = text_content[:2000]
            try:
                summary_resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": f"Пожалуйста, дай краткий анализ или резюме содержимого этого документа:\n{snippet}"}]
                )
                summary = summary_resp.choices[0].message.content
                await send_message(chat_id, f"📄 Резюме документа:\n{summary}")
            except Exception as e:
                await send_message(chat_id, f"Ошибка OpenAI: {e}")
        return {"ok": True}

    # Photo handling
    if photos := msg.get("photo"):
        file_id = photos[-1].get("file_id")
        dest = f"/tmp/{file_id}.jpg"
        await download_telegram_file(file_id, dest)
        await send_message(chat_id, "✅ Фото получено и сохранено.")
        return {"ok": True}

    # Log user and usage if DB enabled
    if database:
        await database.execute(
            "INSERT INTO users (chat_id) VALUES (:chat_id) ON CONFLICT (chat_id) DO NOTHING;",
            {"chat_id": str(chat_id)}
        )
        await database.execute(
            "INSERT INTO usage_log (chat_id) VALUES (:chat_id);",
            {"chat_id": str(chat_id)}
        )

    # Initialize or retrieve conversation thread
    if chat_id not in chat_histories:
        thread = client.beta.threads.create()
        chat_histories[chat_id] = thread.id
    else:
        thread = client.beta.threads.retrieve(chat_histories[chat_id])

    # Commands
    if text == "/start":
        if chat_id not in started_users:
            await send_message(chat_id, "👋 Привет! Я твой BESTFRIEND. Готов помочь! Просто напиши, что тебе нужно.")
            started_users.add(chat_id)
        return {"ok": True}

    if text == "/sub":
        await send_message(chat_id, "💳 Подписка: 399₽/мес или 2990₽/год.\nНапиши 'подписка' для оформления.")
        return {"ok": True}

    if text == "/help":
        await send_message(chat_id, "📖 Просто напиши, что хочешь: вопрос, рисование, новости.")
        return {"ok": True}

    if text == "/admin":
        if chat_id != OWNER_CHAT_ID:
            await send_message(chat_id, "⛔ У тебя нет доступа к этой команде.")
            return {"ok": True}
        if database:
            total_users = await database.fetch_val("SELECT COUNT(*) FROM users;")
            total_requests = await database.fetch_val("SELECT COUNT(*) FROM usage_log;")
            active_subs = await database.fetch_val("SELECT COUNT(*) FROM subscriptions WHERE is_active = true;")
        else:
            total_users = total_requests = active_subs = 0
        stats = (
            "📊 *Статистика:*\n"
            f"👥 Пользователей: {total_users}\n"
            f"📨 Запросов: {total_requests}\n"
            f"💳 Подписок: {active_subs}"
        )
        await send_message(chat_id, stats)
        return {"ok": True}

    # Image generation
    if any(kw in text.lower() for kw in ["нарисуй", "сгенерируй", "картинка", "фото"]):
        try:
            resp = client.images.generate(model="dall-e-3", prompt=text, n=1, size="1024x1024")
            url = resp.data[0].url
            async with httpx.AsyncClient() as client_http:
                await client_http.post(
                    f"{TELEGRAM_API}/sendPhoto",
                    json={"chat_id": chat_id, "photo": url}
                )
        except Exception as e:
            await send_message(chat_id, f"Ошибка генерации изображения: {e}")
        return {"ok": True}

    # News via SerpAPI
    if any(w in text.lower() for w in ["что нового", "новости"]):
        params = {"q": "новости", "hl": "ru", "gl": "ru", "api_key": SERPAPI_KEY}
        try:
            results = GoogleSearch(params).get_dict().get("news_results", [])
            news = "Не удалось получить новости." if not results else "\n".join(f"• {i['title']}" for i in results[:5])
        except Exception as e:
            news = f"Ошибка получения новостей: {e}"
        await send_message(chat_id, news)
        return {"ok": True}

    # Assistants API conversation
    try:
        client.beta.threads.messages.create(thread_id=thread.id, role="user", content=text)
        run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=ASSISTANT_ID)
        while True:
            status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if status.status == "completed":
                break
        msgs = client.beta.threads.messages.list(thread_id=thread.id)
        reply = msgs.data[-1].content[0].text.value
        await send_message(chat_id, reply)
    except Exception as e:
        await send_message(chat_id, f"Ошибка общения с OpenAI: {e}")

    return {"ok": True}








