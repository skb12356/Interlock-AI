"""Static production console.

The gateway stays the OpenAI-compatible API surface. This service is intentionally
boring: serve the static console, expose a healthcheck for the native supervisor, and
serve committed JSON artifacts for chart panels. No build step, no node_modules, no
network dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_ROOT = REPO_ROOT / "console"
ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

app = FastAPI(title="Interlock console", version="0.1.0")
app.mount("/assets", StaticFiles(directory=CONSOLE_ROOT), name="console-assets")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "console",
        "static_root": str(CONSOLE_ROOT),
        "artifacts": ARTIFACTS_ROOT.exists(),
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(CONSOLE_ROOT / "index.html")


@app.get("/api/artifacts/{name:path}")
async def artifact(name: str) -> Any:
    path = (ARTIFACTS_ROOT / name).resolve()
    if path.suffix != ".json" or not path.is_file() or ARTIFACTS_ROOT.resolve() not in path.parents:
        return JSONResponse({"error": {"message": f"no JSON artifact {name!r}"}}, status_code=404)
    return json.loads(path.read_text(encoding="utf-8"))
