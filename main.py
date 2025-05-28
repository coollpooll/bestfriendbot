from fastapi import FastAPI, Request
import requests

app = FastAPI()

TELEGRAM_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")
        
        if text == "/start":
            welcome = (
                "👋 Привет! Я твой BEST FRIEND — ИИ-бот, который заменяет любые платные курсы.\n"
                "Отвечаю голосом, текстом, создаю картинки, обучаю по шагам.\n"
                "💸 3 запроса в день — бесплатно. Подписка: 399₽ в месяц или 3990₽ в год.\n"
                "Без воды, без инфоцыган.\n"
                "Попробуй прямо сейчас — спроси, и я сделаю для тебя личный курс!"
            )
            requests.post(API_URL, json={"chat_id": chat_id, "text": welcome})
    
    return {"ok": True}

