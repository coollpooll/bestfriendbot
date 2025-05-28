import os
from openai import OpenAI
from fastapi import FastAPI, Request
from pydantic import BaseModel
import httpx
from serpapi import GoogleSearch

app = FastAPI()

BOT_TOKEN = "7699903458:AAEGl6YvcYpFTFh9-D61JSYeWGA9blqiOyc"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 
SERPAPI_KEY = "292bb3653ec4db2e9abc418bc91548b1fec768997bf9f1aec3937f426272ae29"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

client = OpenAI(api_key=OPENAI_API_KEY)
usage_counter = {}
chat_histories = {}

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
        {"command": "help", "description": "Инструкция"}
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
    headlines = [f"\u2022 {item['title']}" for item in news_results[:5]]
    return "\n".join(headlines)

async def generate_dalle(prompt):
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        return response.data[0].url
    except Exception as e:
        return None

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

        if text.startswith("/start"):
            await update_bot_commands()
            await send_message(chat_id,
                """\U0001F44B Привет, я BEST FRIEND 🤖 — я твой личный ИИ, который не ищет в тебе выгоду, не уговаривает, не льстит.

🎓 Заменяю любые платные курсы.
🧠 Отвечаю как GPT-4.
🎨 Рисую картинки.
🎥 Скоро — видео.

🌀 3 запроса каждый день — бесплатно.
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

        user_id = str(chat_id)
        is_owner = user_id == "520740282"

        if not is_owner:
            usage_key = f"user_usage:{user_id}"
            count = usage_counter.get(usage_key, 0)
            if count >= 3:
                await send_message(chat_id, "❌ Лимит исчерпан. 3 запроса в день бесплатно.\n\nОформи подписку за 399₽ и пользуйся без ограничений.")
                return {"ok": True}
            usage_counter[usage_key] = count + 1

        if any(kw in text.lower() for kw in ["нарисуй", "сгенерируй", "сделай картинку", "покажи изображение", "фото", "изображение"]):
            image_url = await generate_dalle(text)
            if image_url:
                async with httpx.AsyncClient() as client_http:
                    await client_http.post(f"{TELEGRAM_API}/sendPhoto", json={"chat_id": chat_id, "photo": image_url})
            else:
                await send_message(chat_id, "❌ Не удалось создать изображение. Попробуй переформулировать запрос.")
            return {"ok": True}

        if "что нового" in text.lower() or "новости" in text.lower():
            news = get_latest_news()
            await send_message(chat_id, news)
            return {"ok": True}

        history = chat_histories.get(user_id, [])
        history.append({"role": "user", "content": text})
        if len(history) > 20:
            history = history[-20:]

        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=history,
            temperature=0.7
        )
        reply = completion.choices[0].message.content
        history.append({"role": "assistant", "content": reply})
        chat_histories[user_id] = history

        await send_message(chat_id, reply)

    except Exception as e:
        await send_message(chat_id, f"⚠️ Ошибка: {str(e)}")

    return {"ok": True}












