"""Connect a real MCP client over HTTP to the running API, list tools and call one.

Start the API first (in another terminal, from backend/):
    uv run python -m uvicorn app.main:app
with MCP_TOKEN set in backend/.env. Then:
    uv run python scripts/mcp_smoke.py
"""

import asyncio
import sys
from pathlib import Path

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models import Project  # noqa: E402


async def main() -> None:
    token = get_settings().mcp_token
    if token is None:
        sys.exit("Set MCP_TOKEN in backend/.env (any long random string) and restart the API.")
    async with SessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.name == "PayStream"))
    url = f"http://127.0.0.1:8000/mcp/incidents/?project={project.id}&ref=v1.4.8"
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"}
    for label, http_headers in (("with the token", headers), ("without a token", {})):
        async with httpx2.AsyncClient(headers=http_headers, timeout=60) as http:
            async with streamable_http_client(url, http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    if http_headers:
                        tools = (await client.list_tools()).tools
                        print("tools:", [t.name for t in tools])
                    result = await client.call_tool("get_incident", {"key": "INC-017"})
                    print(f"{label}: get_incident INC-017 ->", result.content[0].text[:250])


asyncio.run(main())
