import os
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    chat_id = data.get("message", {}).get("chat", {}).get("id")
    text = data.get("message", {}).get("text")
    if not chat_id or not text:
        return {"ok": True}
    # Заглушка: ответ на любое сообщение
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Бот работает! Жду команду или файл."}
        )
    return {"ok": True}









