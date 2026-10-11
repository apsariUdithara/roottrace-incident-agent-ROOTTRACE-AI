from fastapi import FastAPI

from app.core.config import get_settings

get_settings()  # fail fast on invalid config

app = FastAPI(title="RootTrace AI", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
