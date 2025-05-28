import os
import json
from datetime import datetime
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx

app = FastAPI()

BOT_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

client = OpenAI(api_key=OPENAI_API_KEY)

LIMITS_FILE = "limits.json"

class TelegramMessage(BaseModel):
    update_id: int
    message: dict = None

def load_limits():
    if os.path.exists(LIMITS_FILE):
        with open(LIMITS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_limits(data):
    with open(LIMITS_FILE, "w") as f:
        json.dump(data, f)

def check_limit(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    data = load_limits()
    user_data = data.get(str(user_id), {})
    if user_data.get("date") != today:
        data[str(user_id)] = {"date": today, "count": 1}
        save_limits(data)
        return True
    elif user_data["count"] < 3:
        data[str(user_id)]["count"] += 1
        save_limits(data)
        return True
    else:
        return False

async def send_message(chat_id, text):
    async with httpx.AsyncClient() as client_http:
        await client_http.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })

async def send_voice(chat_id, audio_bytes):
    files = {"voice": ("voice.mp3", audio_bytes)}
    async with httpx.AsyncClient() as client_http:
        await client_http.post(f"{TELEGRAM_API}/sendVoice", data={"chat_id": chat_id}, files=files)

async def generate_speech(text):
    response = await client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text
    )
    return await response.read()

async def generate_dalle(prompt):
    response = await client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        n=1,
        size="1024x1024"
    )
    return response.data[0].url

@app.post("/webhook")
async def telegram_webhook(req: Request):
    body = await req.json()
    update = TelegramMessage(**body)

    if not update.message:
        return {"ok": True}

    try:
        msg = update.message
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            await send_message(chat_id,
                "👋 Привет,я BEST FRIEND 🤖 — я твой личный ИИ, который делает не ищет в тебе выгоду, не уговаривает, не льстит.\n\n"
                "🎓 Заменяю любые платные курсы.\n"
                "🧠 Отвечаю как GPT-4.\n"
                "🎤 Говорю голосом.\n"
                "🎨 Рисую картинки.\n"
                "🎥 Скоро — видео.\n\n"
                "🆓 3 запроса каждый день — бесплатно.\n"
                "💳 Подписка: 399₽/мес или 2990₽/год.\n\n"
                "Начни с любого запроса. Я уже жду."
            )
            return {"ok": True}

        if not check_limit(user_id):
            await send_message(chat_id, "🚫 Лимит 3 бесплатных запросов в сутки исчерпан. Оформи подписку, чтобы продолжить 🧠")
            return {"ok": True}

        if text.startswith("/скажи"):
            query = text.replace("/скажи", "").strip()
            if query:
                audio = await generate_speech(query)
                await send_voice(chat_id, audio)
            else:
                await send_message(chat_id, "🔊 Напиши что озвучить: `/скажи твой текст`")
        elif text.startswith("/сгенерируй"):
            prompt = text.replace("/сгенерируй", "").strip()
            if prompt:
                image_url = await generate_dalle(prompt)
                async with httpx.AsyncClient() as client_http:
                    await client_http.post(f"{TELEGRAM_API}/sendPhoto", json={"chat_id": chat_id, "photo": image_url})
            else:
                await send_message(chat_id, "🖼 Введи запрос: `/сгенерируй девушка в балаклаве на фоне города`")
        else:
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": text}],
                temperature=0.7
            )
            reply = completion.choices[0].message.content
            await send_message(chat_id, reply)

    except Exception as e:
        await send_message(chat_id, f"⚠️ Ошибка: {str(e)}")

    return {"ok": True}








