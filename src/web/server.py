"""内嵌 HTTP 服务：提供 /api/status 和 dashboard 页面."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web

from src.core.logging import get_logger
from src.web.status_store import get_store

logger = get_logger("web.server")

_HTML_PATH = Path(__file__).parent / "dashboard.html"


async def _handle_status(request: web.Request) -> web.Response:
    store = get_store()
    if store is None:
        return web.json_response({"error": "not initialized"}, status=503)
    data = store.to_dict()
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def _handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_HTML_PATH)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/status", _handle_status)
    return app


async def start_server(host: str = "127.0.0.1", port: int = 8080) -> web.AppRunner:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("dashboard_server_started", url=f"http://{host}:{port}")
    print(f"\n  [Dashboard] http://{host}:{port}\n")
    return runner
