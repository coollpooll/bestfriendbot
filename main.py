import os
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx
import json
from serpapi import GoogleSearch

app = FastAPI()

BOT_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SERPAPI_KEY = "292bb3653ec4db2e9abc418bc91548b1fec768997bf9f1aec3937f426272ae29"
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
    headlines = [f"\u2022 {item['title']}" for item in news_results[:5]]
    return "\n".join(headlines)

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
        return {"ok": True}

    try:
        msg = update.message
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        await send_message(chat_id, f"\u2705 Твой chat_id: `{chat_id}`")

        if text.startswith("/start"):
            await send_message(chat_id, 
                """\ud83d\udc4b Привет, я BEST FRIEND \ud83e\udd16 \u2014 я твой личный ИИ, который не ищет в тебе выгоду, не уговаривает, не льстит.

\ud83c\udf93 Заменяю любые платные курсы.
\ud83e\udde0 Отвечаю как GPT-4.
\ud83c\udfa4 Говорю голосом.
\ud83c\udfa8 Рисую картинки.
\ud83c\udfa5 Скоро \u2014 видео.

\ud83c\udd7f\ufe0f 3 запроса каждый день \u2014 бесплатно.
\ud83d\udcb3 Подписка: 399\u20bd/мес или 2990\u20bd/год.

Начни с любого запроса. Я уже жду."""
            )
        elif text.startswith("/скажи"):
            query = text.replace("/скажи", "").strip()
            if query:
                audio = await generate_speech(query)
                await send_voice(chat_id, audio)
            else:
                await send_message(chat_id, "\ud83d\udd0a Напиши что озвучить: `/скажи твой текст`")
        else:
            user_id = str(chat_id)
            is_owner = user_id == "520740282"

            if not is_owner:
                usage_key = f"user_usage:{user_id}"
                count = usage_counter.get(usage_key, 0)
                if count >= 3:
                    await send_message(chat_id, "\u274c Лимит исчерпан. 3 запроса в день бесплатно.\n\nОформи подписку за 399\u20bd и пользуйся без ограничений.")
                    return
                usage_counter[usage_key] = count + 1

            if any(kw in text.lower() for kw in ["нарисуй", "сгенерируй", "сделай картинку", "покажи изображение"]):
                image_url = await generate_dalle(text)
                async with httpx.AsyncClient() as client_http:
                    await client_http.post(f"{TELEGRAM_API}/sendPhoto", json={"chat_id": chat_id, "photo": image_url})
                return {"ok": True}

            if "что нового" in text.lower() or "новости" in text.lower():
                news = get_latest_news()
                await send_message(chat_id, news)
                return {"ok": True}

            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": text}],
                temperature=0.7
            )
            reply = completion.choices[0].message.content
            await send_message(chat_id, reply)

    except Exception as e:
        await send_message(chat_id, f"\u26a0\ufe0f Ошибка: {str(e)}")

    return {"ok": True}




