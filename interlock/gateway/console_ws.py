"""Console websocket — **Person 2 owns this file** (see `coordination/ALLOTED_WORK.md`).

It exists and is already mounted in `app.py` for one reason: so that building the
console never requires editing `app.py`. That is the only file the two work streams
would otherwise collide on, and the collision has been removed in advance rather than
resolved afterwards.

Everything below is a working skeleton, not a placeholder. It broadcasts real decision
events to connected clients today; what it does not yet do is anything opinionated about
what the console wants to see. Reshape it freely — the only things that must not change
are the event *names* and *payload shapes*, which are Contract 3 and frozen.

    ws://127.0.0.1:8080/console/ws

**Invariant 2 applies here as much as in the UI.** This socket pushes decisions that
have already been made. It must never grow an inbound message that sets a threshold,
overrides an action, or asks the operator to choose a number. Approve/reject on a hold
goes through the existing REST endpoints, which are audited and durable; a websocket
command would not be.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

__all__ = ["ConsoleHub", "router"]

#: Recent events replayed to a client that connects mid-conversation, so a console
#: opened at the wrong moment does not show an empty screen for the whole demo.
REPLAY_BUFFER = 200


@dataclass
class ConsoleHub:
    """Fan-out of interlock events to every connected console.

    Deliberately fire-and-forget. A console that is slow, wedged or gone must never
    slow down or fail a customer's request -- the observability path is not allowed to
    become a dependency of the token path. A send that fails drops the client, and the
    request never learns about it.
    """

    _clients: set[WebSocket] = field(default_factory=set, init=False)
    _recent: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=REPLAY_BUFFER), init=False
    )

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        for event in list(self._recent):
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps(event))

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        """Called from the request path. **Synchronous and non-blocking on purpose.**"""
        event = {"event": event_name, "data": payload}
        self._recent.append(event)
        if not self._clients:
            return
        with contextlib.suppress(RuntimeError):  # no running loop, e.g. in a sync test
            asyncio.get_running_loop().create_task(self._broadcast(event))

    async def _broadcast(self, event: dict[str, Any]) -> None:
        text = json.dumps(event)
        dead: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)
        for client in dead:
            self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def recent(self) -> list[dict[str, Any]]:
        """Snapshot of the replay buffer for HTTP-only console clients."""
        return list(self._recent)


router = APIRouter(prefix="/console", tags=["console"])


@router.websocket("/ws")
async def console_ws(websocket: WebSocket) -> None:
    """Push-only. Inbound frames are read solely to detect disconnects."""
    hub: ConsoleHub = websocket.app.state.console_hub
    await hub.connect(websocket)
    try:
        while True:
            # Nothing inbound is acted on -- see the invariant-2 note in the module
            # docstring. This await exists to notice the client going away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(websocket)


@router.get("/recent")
async def recent_events(request: Request) -> dict[str, Any]:
    """The replay buffer over plain HTTP.

    Here so the console can be built and debugged with `curl` before any websocket code
    is written, and so a browser with a blocked websocket still has something to render.
    """
    hub: ConsoleHub = request.app.state.console_hub
    return {"events": hub.recent()}
