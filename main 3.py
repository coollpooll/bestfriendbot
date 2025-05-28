
import os
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx

app = FastAPI()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

client = OpenAI(api_key=OPENAI_API_KEY)

class TelegramMessage(BaseModel):
    update_id: int
    message: dict = None

async def send_message(chat_id, text):
    async with httpx.AsyncClient() as http_client:
        await http_client.post(f"{TELEGRAM_API}/sendMessage", json={{
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }})

async def send_voice(chat_id, audio_bytes):
    files = {{"voice": ("voice.mp3", audio_bytes)}}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(f"{TELEGRAM_API}/sendVoice", data={{"chat_id": chat_id}}, files=files)

async def generate_speech(text):
    response = client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text
    )
    return response.content

async def generate_dalle(prompt):
    response = client.images.generate(
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
        return {{"ok": True}}

    msg = update.message
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    if text.startswith("/start"):
        await send_message(chat_id,
            "👋 Привет! Я — BEST FRIEND 🤖\n\n"
            "Я заменяю любые курсы: GPT-4, голос, картинки и даже видео. 3 запроса в день — бесплатно. Подписка: 399₽/мес или 2990₽/год. Начни с запроса!"
        )
    elif text.startswith("/скажи"):
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
            async with httpx.AsyncClient() as http_client:
                await http_client.post(f"{TELEGRAM_API}/sendPhoto", json={{"chat_id": chat_id, "photo": image_url}})
        else:
            await send_message(chat_id, "🖼 Введи запрос: `/сгенерируй девушка в балаклаве на фоне города`")
    else:
        response = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {{"role": "system", "content": "Ты — честный и дерзкий помощник, всегда говоришь по делу."}},
                {{"role": "user", "content": text}}
            ],
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()
        await send_message(chat_id, reply)

    return {{"ok": True}}
