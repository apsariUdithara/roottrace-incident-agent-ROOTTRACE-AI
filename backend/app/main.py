from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import auth, chat, projects
from app.core.config import get_settings
from app.core.db import SessionDep, engine
from app.core.errors import install_error_handlers
from app.mcp_servers import MOUNTS

settings = get_settings()  # also fails fast on invalid config


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        for mount in MOUNTS.values():  # each MCP server runs for the app's lifetime
            await stack.enter_async_context(mount.running())
        yield
    await engine.dispose()


app = FastAPI(title="RootTrace AI", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
# Only the frontend may call the API from a browser. In production it is served from the same
# origin through Caddy, so this mostly guards against other sites calling the API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,  # the refresh-token cookie
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],  # Last-Event-ID: SSE resume
)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(chat.router)
# RootTrace's tools for any MCP client: /mcp/github, /mcp/docs, /mcp/incidents
for _name, _mount in MOUNTS.items():
    app.mount(f"/mcp/{_name}", _mount)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(session: SessionDep, response: Response) -> dict[str, str]:
    """Readiness: the database answers."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}
