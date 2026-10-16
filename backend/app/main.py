from fastapi import FastAPI

from app.api.routes import chat
from app.core.config import get_settings

get_settings()  # fail fast on invalid config

app = FastAPI(title="RootTrace AI", version="0.1.0")
app.include_router(chat.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
