
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()

@app.get("/")
def root():
    return {"status": "Bot is running"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
