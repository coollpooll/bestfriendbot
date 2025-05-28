import os
import asyncio
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx
import json

app = FastAPI()

BOT_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

client = OpenAI(api_key=OPENAI_API_KEY)
usage_counter = {}

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

def generate_dalle(prompt):
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
    print(json.dumps(body, indent=2))
    update = TelegramMessage(**body)

    if not update.message:
        return {"ok": True}

    try:
        msg = update.message
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        await send_message(chat_id, f"✅ Твой chat_id: `{chat_id}`")

        if text.startswith("/start"):
            await send_message(chat_id,
                "👋 Привет, я BEST FRIEND 🤖 — я твой личный ИИ, который делает не ищет в тебе выгоду, не уговаривает, не льстит.\n\n"
                "🎓 Заменяю любые платные курсы.\n"
                "🧠 Отвечаю как GPT-4.\n"
                "🎤 Говорю голосом.\n"
                "🎨 Рисую картинки.\n"
                "🎥 Скоро — видео.\n\n"
                "🆓 3 запроса каждый день — бесплатно.\n"
                "💳 Подписка: 399₽/мес или 2990₽/год.\n\n"
                "Начни с любого запроса. Я уже жду."
            )
        elif text.startswith("/скажи"):
            query = text.replace("/скажи", "").strip()
            if query:
                audio = await generate_speech(query)
                await send_voice(chat_id, audio)
            else:
                await send_message(chat_id, "🔊 Напиши что озвучить: `/скажи твой текст`")
        else:
            user_id = str(chat_id)
            is_owner = user_id == "520740282"

            if not is_owner:
                usage_key = f"user_usage:{user_id}"
                count = usage_counter.get(usage_key, 0)
                if count >= 3:
                    await send_message(chat_id, "❌ Лимит исчерпан. 3 запроса в день бесплатно.\n\nОформи подписку за 399₽ и пользуйся без ограничений.")
                    return
                usage_counter[usage_key] = count + 1

            # Проверка на генерацию изображения по содержанию текста
            if any(kw in text.lower() for kw in ["нарисуй", "сгенерируй", "сделай картинку", "покажи изображение"]):
                loop = asyncio.get_event_loop()
                image_url = await loop.run_in_executor(None, lambda: generate_dalle(text))
                async with httpx.AsyncClient() as client_http:
                    await client_http.post(f"{TELEGRAM_API}/sendPhoto", json={"chat_id": chat_id, "photo": image_url})
                return {"ok": True}

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

