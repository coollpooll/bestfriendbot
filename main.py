import os
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "520740282"))

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "")

    # Если это документ
    if msg.get("document"):
        await send_message(chat_id, "Файл получен! (позже добавим обработку)")
        return {"ok": True}

    # Если это фото
    if msg.get("photo"):
        await send_message(chat_id, "Фото получено! (позже добавим обработку)")
        return {"ok": True}

    # Команды
    if text == "/start":
        await send_message(chat_id, "👋 Привет! Я твой BESTFRIEND. Готов помочь! Просто напиши, что тебе нужно.")
    elif text == "/sub":
        await send_message(chat_id, "💳 Подписка: 399₽/мес или 2990₽/год.\nНапиши 'подписка' для оформления.")
    elif text == "/help":
        await send_message(chat_id, "📖 Просто напиши, что хочешь: вопрос, рисование, новости.")
    elif text == "/admin":
        if chat_id == OWNER_CHAT_ID:
            await send_message(chat_id, "📊 Статистика будет тут (добавим позже)")
        else:
            await send_message(chat_id, "⛔ У тебя нет доступа к этой команде.")
    else:
        await send_message(chat_id, "✅ Бот работает! Жду команду или файл.")
    return {"ok": True}

async def send_message(chat_id, text):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text}
        )










