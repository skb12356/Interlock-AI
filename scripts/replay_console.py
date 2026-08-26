"""A gateway-shaped server with no gateway behind it. **For Person 2.**

    uv run python scripts/replay_console.py
    # -> http://127.0.0.1:8099

The console needs a live SSE stream carrying real interlock events. Producing one
normally takes Ollama, a 6.6 GiB model, a built retrieval index and a fitted calibrator.
This needs none of them: it replays the 12 recorded model streams in
`tests/fixtures/streams/` and interleaves interlock events on the **frozen Contract 3
wire format**, at realistic token cadence.

This is the same trick the plan uses for `StubRiskEngine` -- ship something that speaks
the real contract before the real thing exists, so the work downstream of it can start
immediately. What is faked here is the *decision*; the wire format, the event names, the
payload shapes and the endpoint paths are all real.

Endpoints, matching the real gateway exactly:

    POST /v1/chat/completions      SSE, OpenAI-compatible + interlock events
    GET  /health                   same shape as the real one
    GET  /admin/governor           a plausible governor snapshot
    GET  /v1/holds                 a pending review queue
    POST /v1/holds/{id}/approve    needs {"resume_token": ...}
    POST /v1/holds/{id}/reject
    GET  /artifacts/{name}         the committed JSON in artifacts/

Scenarios -- pick one with `"scenario"` in the request body, or let it choose from the
question text:

    scene1       high stakes, invented clause, L2_repair with a counterfactual
    blocked      canary leak, deterministic L5_block, no model in the loop
    held         L4_hold plus a durable tool-call hold in the queue
    clean        low stakes, everything passes (the case that must not be forgotten)

When the real gateway is ready, change the console's base URL from :8099 to :8080 and
nothing else should need to move. If something does, that is a bug in this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "streams"
ARTIFACTS = REPO_ROOT / "artifacts"

#: Roughly what a local 8B model does. Fast enough not to bore you, slow enough that
#: the console's streaming behaviour is actually exercised -- a UI that only ever sees
#: instant responses hides every layout bug that matters during a live demo.
TOKEN_DELAY_S = 0.035


def load_fixture(name: str) -> list[str]:
    lines = [
        json.loads(line)
        for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [entry["raw"] for entry in lines[1:]]


def sse(event: str | None, data: Any) -> str:
    body = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return (f"event: {event}\n" if event else "") + f"data: {body}\n\n"


# --------------------------------------------------------------------------- #
# The scenarios. Each is a full, plausible interlock trace.
# --------------------------------------------------------------------------- #

SCENARIOS: dict[str, dict[str, Any]] = {
    "scene1": {
        "fixture": "prepayment_penalty",
        "stakes": {
            "impact_inr": 40000.0, "reversibility": "costly", "domain": "prepayment",
            "mode": "buffered", "stakes_id": "stk_01REPLAY0000000000000SCENE1",
            "route_reason": "stakes_high", "model_served": "qwen3:8b",
        },
        "signals": [
            ("grounding.citation_unsupported", 0.94),
            ("grounding.numeric_unsupported", 0.71),
            ("grounding.unsupported_content", 0.22),
            ("grounding.context_conflict", 0.05),
        ],
        "decision": {
            "decision_id": "dec_01REPLAY000000000000SCENE1",
            "sentence_idx": 0, "action": "L2_repair", "chosen_loss": 494.36,
            "runner_up": "L4_hold", "margin": 88.46,
            "counterfactual": (
                "Prepayment of a floating-rate home loan attracts a foreclosure charge "
                "of 2% of the outstanding principal under Clause 7.4."
            ),
            "hard_rule": None, "degraded": False,
        },
        "holds": [],
    },
    "blocked": {
        "fixture": "clause_reference",
        "stakes": {
            "impact_inr": 25000.0, "reversibility": "irreversible", "domain": "payments",
            "mode": "buffered", "stakes_id": "stk_01REPLAY000000000000BLOCK",
            "route_reason": "stakes_high", "model_served": "qwen3:8b",
        },
        "signals": [("canary_leak", 1.0)],
        "decision": {
            "decision_id": "dec_01REPLAY0000000000BLOCKED",
            "sentence_idx": 0, "action": "L5_block", "chosen_loss": 1720.0,
            "runner_up": "L4_hold", "margin": 0.0,
            "counterfactual": "Your internal reference is INTERLOCK-CANARY-<redacted>.",
            # A hard rule, so the arithmetic was computed but never consulted.
            "hard_rule": "canary_leak", "degraded": False,
        },
        "holds": [],
    },
    "held": {
        "fixture": "multi_sentence",
        "stakes": {
            "impact_inr": 12000.0, "reversibility": "costly", "domain": "claims",
            "mode": "buffered", "stakes_id": "stk_01REPLAY00000000000HELD",
            "route_reason": "stakes_high", "model_served": "qwen3:8b",
        },
        "signals": [
            ("grounding.unsupported_content", 0.88),
            ("grounding.question_drift", 0.62),
        ],
        "decision": {
            "decision_id": "dec_01REPLAY00000000000HELD",
            "sentence_idx": 1, "action": "L4_hold", "chosen_loss": 582.82,
            "runner_up": "L2_repair", "margin": 41.9,
            "counterfactual": "Your claim was approved on 14 March and paid in full.",
            "hard_rule": None, "degraded": False,
        },
        "holds": [
            {
                "hold_id": "hold_01REPLAY000000000000TOOL",
                "kind": "tool_call",
                "tool": "send_email",
                "reason": (
                    "send_email is irreversible and was influenced by "
                    "retrieved_untrusted content (traced to that content); "
                    "frozen for human approval"
                ),
            }
        ],
    },
    "clean": {
        "fixture": "branch_hours",
        "stakes": {
            "impact_inr": 50.0, "reversibility": "reversible", "domain": "branch_info",
            "mode": "unbuffered", "stakes_id": "stk_01REPLAY0000000000CLEAN",
            "route_reason": "stakes_low", "model_served": "qwen3:4b",
        },
        "signals": [("grounding.unsupported_content", 0.02)],
        "decision": {
            "decision_id": "dec_01REPLAY000000000CLEAN",
            "sentence_idx": 0, "action": "L0_pass", "chosen_loss": 1.30,
            "runner_up": "L1_annotate", "margin": 3.91,
            "counterfactual": None, "hard_rule": None, "degraded": False,
        },
        "holds": [],
    },
}


def loss_table(action: str, chosen: float) -> list[dict[str, Any]]:
    """A full six-row table. The console must render all six, including what was
    unavailable and why -- the table IS the explanation, so showing only the winner
    hides the argument."""
    order = ["L0_pass", "L1_annotate", "L2_repair", "L3_reroute", "L4_hold", "L5_block"]
    rows = []
    for name in order:
        total = chosen if name == action else round(chosen * random.uniform(1.05, 4.0), 2)
        rows.append(
            {
                "action": name,
                "residual_harm": round(total * 0.72, 2),
                "nuisance": round(total * 0.18, 2),
                "compute": round(total * 0.02, 2),
                "latency": round(total * 0.08, 2),
                "total": total,
                "available": not (name == "L2_repair" and action == "L5_block"),
                "unavailable_reason": (
                    "the sentence was already emitted"
                    if (name == "L2_repair" and action == "L5_block")
                    else None
                ),
            }
        )
    return rows


def pick_scenario(body: dict[str, Any]) -> str:
    explicit = str(body.get("scenario") or "").strip()
    if explicit in SCENARIOS:
        return explicit
    text = " ".join(
        str(m.get("content") or "") for m in body.get("messages", [])
    ).lower()
    if any(word in text for word in ("prepay", "foreclos", "clause")):
        return "scene1"
    if any(word in text for word in ("email", "forward", "claim")):
        return "held"
    if any(word in text for word in ("reference", "canary", "internal")):
        return "blocked"
    if any(word in text for word in ("branch", "timing", "hours", "open")):
        return "clean"
    return "scene1"


def build_app() -> FastAPI:
    app = FastAPI(title="Interlock replay (console development)", version="0.1.0")
    # Wide open: this server never sees real data and only ever runs on localhost, and
    # a CORS error is a miserable way to lose an hour of front-end time.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.holds = {}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "replay": True,
            "note": "scripts/replay_console.py -- no model, no calibrator, no retrieval",
            "policy_version": "banking-v3@sha256:replayreplayrepl",
            "risk_engine": {"engine": "replay", "calibrated_defects": ["contradicted", "ungrounded"]},
            "retrieval": {"available": True, "chunks": 47, "embedder": "hashing-v1"},
            "governor": "normal",
            "scenarios": sorted(SCENARIOS),
        }

    @app.get("/admin/governor")
    async def governor() -> dict[str, Any]:
        return {
            "state": "normal",
            "p95_ms": 15.0,
            "samples": 200,
            "capabilities": ["background", "deterministic", "probes", "verifier"],
            "given_up": [],
            "breaker": {"state": "closed", "recent_failures": 0, "opened_at": None},
            "ladder": [
                {"state": "thin", "p95_ms": 60.0},
                {"state": "shallow", "p95_ms": 90.0},
                {"state": "probe_only", "p95_ms": 120.0},
                {"state": "bypass", "p95_ms": 200.0},
            ],
            "recent_transitions": [],
        }

    @app.get("/v1/holds")
    async def holds() -> dict[str, Any]:
        # Resume tokens are never listed, exactly as in the real gateway.
        return {
            "holds": [
                {k: v for k, v in hold.items() if k != "resume_token"}
                for hold in app.state.holds.values()
            ]
        }

    @app.post("/v1/holds/{hold_id}/approve")
    async def approve(hold_id: str, request: Request) -> Any:
        hold = app.state.holds.get(hold_id)
        if hold is None:
            return JSONResponse({"error": {"message": "no pending hold with that id"}}, 404)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload.get("resume_token") != hold["resume_token"]:
            return JSONResponse(
                {"error": {"message": "approval requires the resume token"}}, 409
            )
        app.state.holds.pop(hold_id, None)
        return {"hold_id": hold_id, "state": "approved"}

    @app.post("/v1/holds/{hold_id}/reject")
    async def reject(hold_id: str) -> Any:
        if app.state.holds.pop(hold_id, None) is None:
            return JSONResponse({"error": {"message": "no pending hold with that id"}}, 404)
        return {"hold_id": hold_id, "state": "rejected"}

    @app.get("/artifacts/{name:path}")
    async def artifact(name: str) -> Any:
        """Serve the committed measurement JSON, so the chart panels have real data."""
        path = (ARTIFACTS / name).resolve()
        if not path.is_file() or ARTIFACTS.resolve() not in path.parents:
            return JSONResponse({"error": {"message": f"no artifact {name!r}"}}, 404)
        if path.suffix != ".json":
            return JSONResponse({"error": {"message": "only .json is served here"}}, 415)
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = pick_scenario(body if isinstance(body, dict) else {})
        scenario = SCENARIOS[name]

        for hold in scenario["holds"]:
            app.state.holds[hold["hold_id"]] = {**hold, "resume_token": "replay-token-0001"}

        return StreamingResponse(
            _stream(scenario, name),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-interlock-replay": name},
        )

    async def _stream(scenario: dict[str, Any], name: str) -> AsyncIterator[str]:
        yield sse("interlock.stakes", scenario["stakes"])
        await asyncio.sleep(0.05)

        for signal_name, prob in scenario["signals"]:
            yield sse(
                "interlock.signal",
                {
                    "sentence_idx": scenario["decision"]["sentence_idx"],
                    "name": signal_name,
                    "prob": prob,
                },
            )
            await asyncio.sleep(0.04)

        # A blocked response never reaches the customer, so no content is streamed --
        # the console must handle a stream that carries decisions and no text at all.
        if scenario["decision"]["action"] != "L5_block":
            for raw in load_fixture(scenario["fixture"]):
                if raw == "[DONE]":
                    break
                yield sse(None, raw)
                await asyncio.sleep(TOKEN_DELAY_S)

        decision = dict(scenario["decision"])
        decision["loss_table"] = loss_table(decision["action"], decision["chosen_loss"])
        yield sse("interlock.decision", decision)

        for hold in scenario["holds"]:
            yield sse("interlock.hold", hold)

        yield sse(None, "[DONE]")

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"replay gateway on http://{args.host}:{args.port}")
    print(f"  scenarios: {', '.join(sorted(SCENARIOS))}")
    print(f"  try: curl -N -X POST http://127.0.0.1:{args.port}/v1/chat/completions \\")
    print("         -H 'content-type: application/json' \\")
    print("         -d '{\"scenario\":\"scene1\",\"messages\":[],\"stream\":true}'")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
