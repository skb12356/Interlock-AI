"""Production host for the built React console and its same-origin gateway proxy."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_ROOT = REPO_ROOT / "console" / "dist"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def gateway_websocket_url(gateway_url: str) -> str:
    """Translate the configured HTTP gateway base into its console websocket URL."""
    parsed = urlsplit(gateway_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/console/ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def same_origin_websocket(origin: str | None, host: str, *, scheme: str = "ws") -> bool:
    """WebSocket handshakes are not protected by CORS; enforce browser same-origin."""
    if not origin:
        return True
    expected_scheme = "https" if scheme in {"wss", "https"} else "http"
    return origin.rstrip("/") == f"{expected_scheme}://{host}"


def _forward_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value for name, value in headers.items() if name.lower() not in HOP_BY_HOP_HEADERS
    }


def create_console_app(
    *,
    dist_root: Path = DIST_ROOT,
    gateway_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create the console host without coupling import-time state to a build directory."""
    resolved_gateway = (
        gateway_url or os.getenv("INTERLOCK_GATEWAY_URL") or DEFAULT_GATEWAY_URL
    ).rstrip("/")
    built = (dist_root / "index.html").is_file()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.proxy_client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
        )
        try:
            yield
        finally:
            await application.state.proxy_client.aclose()

    application = FastAPI(title="Interlock console", version="0.1.0", lifespan=lifespan)

    @application.get("/health")
    async def health() -> Any:
        payload = {
            "ok": built,
            "service": "console",
            "built": built,
            "gateway": resolved_gateway,
        }
        return payload if built else JSONResponse(payload, status_code=503)

    async def proxy_http(request: Request, upstream_path: str) -> StreamingResponse:
        client: httpx.AsyncClient = request.app.state.proxy_client
        url = f"{resolved_gateway}/{upstream_path.lstrip('/')}"
        upstream_request = client.build_request(
            request.method,
            url,
            params=request.query_params,
            headers={
                name: value
                for name, value in request.headers.items()
                if name.lower() not in HOP_BY_HOP_HEADERS | {"host"}
            },
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=_forward_headers(upstream.headers),
            background=BackgroundTask(upstream.aclose),
        )

    @application.api_route(
        "/gateway/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def gateway_proxy(path: str, request: Request) -> StreamingResponse:
        return await proxy_http(request, path)

    @application.api_route(
        "/console/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def console_proxy(path: str, request: Request) -> StreamingResponse:
        return await proxy_http(request, f"console/{path}")

    @application.websocket("/console/ws")
    async def console_websocket(browser: WebSocket) -> None:
        if not same_origin_websocket(
            browser.headers.get("origin"),
            browser.headers.get("host", ""),
            scheme=str(browser.scope.get("scheme", "ws")),
        ):
            await browser.close(code=1008, reason="websocket origin is not allowed")
            return
        await browser.accept()
        try:
            async with connect(gateway_websocket_url(resolved_gateway)) as upstream:

                async def browser_to_gateway() -> None:
                    while True:
                        message = await browser.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])

                async def gateway_to_browser() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await browser.send_bytes(message)
                        else:
                            await browser.send_text(message)

                tasks = {
                    asyncio.create_task(browser_to_gateway()),
                    asyncio.create_task(gateway_to_browser()),
                }
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in pending:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        except WebSocketDisconnect:
            pass
        except Exception:
            with contextlib.suppress(Exception):
                await browser.close(code=1011)

    if built:
        application.mount("/assets", StaticFiles(directory=dist_root / "assets"), name="assets")

    @application.get("/")
    async def index() -> Any:
        if not built:
            return JSONResponse(
                {
                    "error": {
                        "message": "console build is missing; run npm --prefix console run build"
                    }
                },
                status_code=503,
            )
        return FileResponse(dist_root / "index.html")

    return application


app = create_console_app()
