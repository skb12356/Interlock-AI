from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from interlock.console.app import create_console_app, gateway_websocket_url, same_origin_websocket

REPO_ROOT = Path(__file__).resolve().parents[3]


def built_console(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<div id="root"></div><script src="/assets/app.js"></script>', encoding="utf-8"
    )
    (root / "assets" / "app.js").write_text("window.INTERLOCK_REACT=true", encoding="utf-8")
    return root


def test_service_serves_the_built_react_console_and_reports_build_health(tmp_path: Path) -> None:
    app = create_console_app(dist_root=built_console(tmp_path))

    with TestClient(app) as client:
        assert client.get("/").text.startswith('<div id="root">')
        assert client.get("/assets/app.js").text == "window.INTERLOCK_REACT=true"
        assert client.get("/health").json() == {
            "ok": True,
            "service": "console",
            "built": True,
            "gateway": "http://127.0.0.1:8080",
        }


def test_service_reports_an_unbuilt_console_without_serving_source_files(tmp_path: Path) -> None:
    app = create_console_app(dist_root=tmp_path / "missing")

    with TestClient(app) as client:
        assert client.get("/health").status_code == 503
        assert client.get("/").status_code == 503


def test_same_origin_http_proxy_strips_only_the_gateway_prefix(tmp_path: Path) -> None:
    seen: list[tuple[str, str, bytes]] = []

    class SseStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"data: partial\n\n"
            yield b"data: [DONE]\n\n"

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(
            200,
            stream=SseStream(),
            headers={"content-type": "text/event-stream", "x-interlock-request-id": "req_1"},
        )

    app = create_console_app(
        dist_root=built_console(tmp_path),
        gateway_url="http://gateway.test:8080",
        transport=httpx.MockTransport(upstream),
    )

    with TestClient(app) as client:
        chat = client.post("/gateway/v1/chat/completions", content=b'{"stream":true}')
        status = client.get("/console/status")

    assert seen == [
        ("POST", "/v1/chat/completions", b'{"stream":true}'),
        ("GET", "/console/status", b""),
    ]
    assert chat.text.endswith("data: [DONE]\n\n")
    assert chat.headers["x-interlock-request-id"] == "req_1"
    assert status.status_code == 200


def test_websocket_proxy_uses_the_gateway_console_endpoint() -> None:
    assert gateway_websocket_url("http://127.0.0.1:8080") == "ws://127.0.0.1:8080/console/ws"
    assert gateway_websocket_url("https://bank.example/base") == (
        "wss://bank.example/base/console/ws"
    )


def test_production_console_websocket_rejects_cross_site_origins() -> None:
    assert same_origin_websocket(None, "bank.example")
    assert same_origin_websocket("https://bank.example", "bank.example", scheme="wss")
    assert not same_origin_websocket("http://bank.example", "bank.example", scheme="wss")
    assert not same_origin_websocket("https://evil.example", "bank.example")


def test_native_supervisor_builds_react_and_has_no_duplicate_plain_js_console() -> None:
    supervisor = (REPO_ROOT / "scripts" / "up.ps1").read_text(encoding="utf-8")

    assert "npm --prefix console run build" in supervisor
    assert not (REPO_ROOT / "console" / "app.js").exists()
    assert not (REPO_ROOT / "console" / "styles.css").exists()
