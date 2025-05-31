# Используем официальный образ Python
FROM python:3.11-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    ffmpeg \
    libmagic1 \
    unrar \
    && rm -rf /var/lib/apt/lists/*

# Создаём рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем Python-зависимости
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект в контейнер
COPY . .

# Переменные окружения (лучше задать в Render.com через dashboard!)
# ENV BOT_TOKEN=...
# ENV OPENAI_API_KEY=...
# ENV DATABASE_URL=...

# Открываем порт для FastAPI
EXPOSE 10000

# Стартуем приложение (FastAPI через uvicorn)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]

