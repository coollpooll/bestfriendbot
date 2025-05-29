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







