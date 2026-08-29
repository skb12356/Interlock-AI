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
import sys
from collections.abc import AsyncIterator
from itertools import count
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.gateway.console_ws import (  # noqa: E402
    ALLOWED_ARTIFACTS,
    ConsoleHub,
    LiveConsoleSource,
)
from interlock.gateway.console_ws import router as console_router  # noqa: E402

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
            "impact_inr": 40000.0,
            "reversibility": "costly",
            "domain": "prepayment",
            "mode": "buffered",
            "stakes_id": "stk_01REPLAY0000000000000SCENE1",
            "route_reason": "stakes_high",
            "model_served": "qwen3:8b",
        },
        "signals": [
            ("grounding.citation_unsupported", 0.94),
            ("grounding.numeric_unsupported", 0.71),
            ("grounding.unsupported_content", 0.22),
            ("grounding.context_conflict", 0.05),
        ],
        "decision": {
            "decision_id": "dec_01REPLAY000000000000SCENE1",
            "sentence_idx": 0,
            "action": "L2_repair",
            "chosen_loss": 494.36,
            "runner_up": "L4_hold",
            "margin": 88.46,
            "counterfactual": (
                "Prepayment of a floating-rate home loan attracts a foreclosure charge "
                "of 2% of the outstanding principal under Clause 7.4."
            ),
            "hard_rule": None,
            "degraded": False,
        },
        "holds": [],
    },
    "blocked": {
        "fixture": "clause_reference",
        "stakes": {
            "impact_inr": 25000.0,
            "reversibility": "irreversible",
            "domain": "payments",
            "mode": "buffered",
            "stakes_id": "stk_01REPLAY000000000000BLOCK",
            "route_reason": "stakes_high",
            "model_served": "qwen3:8b",
        },
        "signals": [("canary_leak", 1.0)],
        "decision": {
            "decision_id": "dec_01REPLAY0000000000BLOCKED",
            "sentence_idx": 0,
            "action": "L5_block",
            "chosen_loss": 1720.0,
            "runner_up": "L4_hold",
            "margin": 0.0,
            "counterfactual": "Your internal reference is INTERLOCK-CANARY-<redacted>.",
            # A hard rule, so the arithmetic was computed but never consulted.
            "hard_rule": "canary_leak",
            "degraded": False,
        },
        "holds": [],
    },
    "held": {
        "fixture": "multi_sentence",
        "stakes": {
            "impact_inr": 12000.0,
            "reversibility": "costly",
            "domain": "claims",
            "mode": "buffered",
            "stakes_id": "stk_01REPLAY00000000000HELD",
            "route_reason": "stakes_high",
            "model_served": "qwen3:8b",
        },
        "signals": [
            ("grounding.unsupported_content", 0.88),
            ("grounding.question_drift", 0.62),
        ],
        "decision": {
            "decision_id": "dec_01REPLAY00000000000HELD",
            "sentence_idx": 1,
            "action": "L4_hold",
            "chosen_loss": 582.82,
            "runner_up": "L2_repair",
            "margin": 41.9,
            "counterfactual": "Your claim was approved on 14 March and paid in full.",
            "hard_rule": None,
            "degraded": False,
        },
        "holds": [
            {
                "hold_id": "hold_01REPLAY000000000RESPONSE",
                "kind": "response",
                "tool": None,
                "reason": (
                    "The claim-payment statement is unsupported by retrieved evidence; "
                    "the complete response is frozen for human approval before release"
                ),
            }
        ],
    },
    "clean": {
        "fixture": "branch_hours",
        "stakes": {
            "impact_inr": 50.0,
            "reversibility": "reversible",
            "domain": "branch_info",
            "mode": "unbuffered",
            "stakes_id": "stk_01REPLAY0000000000CLEAN",
            "route_reason": "stakes_low",
            "model_served": "qwen3:4b",
        },
        "signals": [("grounding.unsupported_content", 0.02)],
        "decision": {
            "decision_id": "dec_01REPLAY000000000CLEAN",
            "sentence_idx": 0,
            "action": "L0_pass",
            "chosen_loss": 1.30,
            "runner_up": "L1_annotate",
            "margin": 3.91,
            "counterfactual": None,
            "hard_rule": None,
            "degraded": False,
        },
        "holds": [],
    },
}


def loss_table(action: str, chosen: float, runner_up: str, margin: float) -> list[dict[str, Any]]:
    """A full six-row table. The console must render all six, including what was
    unavailable and why -- the table IS the explanation, so showing only the winner
    hides the argument."""
    order = ["L0_pass", "L1_annotate", "L2_repair", "L3_reroute", "L4_hold", "L5_block"]
    rows = []
    step = max(chosen * 0.17, 1.0)
    next_rank = 1
    for name in order:
        if name == action:
            total = chosen
        elif name == runner_up:
            total = round(chosen + margin, 2)
        else:
            next_rank += 1
            total = round(chosen + margin + step * next_rank, 2)
        nuisance = round(total * 0.18, 2)
        compute = round(total * 0.02, 2)
        latency = round(total * 0.08, 2)
        rows.append(
            {
                "action": name,
                "residual_harm": round(total - nuisance - compute - latency, 2),
                "nuisance": nuisance,
                "compute": compute,
                "latency": latency,
                "total": total,
                "available": True,
                "unavailable_reason": None,
            }
        )
    return rows


class ReplayConsoleSource:
    """In-memory implementation of the live read-only projection contract."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def status(self) -> dict[str, Any]:
        return {
            "source": "replay",
            "replay": True,
            "health": {"ok": True, "scenario_count": len(SCENARIOS)},
            "capabilities": {
                "direct_stream": {"available": True},
                "recent_events": {"available": True},
                "decision_details": {"available": True, "eventually_consistent": False},
                "holds": {"available": True, "approval_requires_token": True},
                "artifacts": {
                    name: (ARTIFACTS / name).is_file() for name in sorted(ALLOWED_ARTIFACTS)
                },
                "economics": {
                    "available": False,
                    "reason": "Replay does not fabricate Lane C economics",
                },
            },
        }

    def decision(self, decision_id: str) -> dict[str, Any]:
        decision = self.app.state.decisions.get(decision_id)
        if decision is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="decision is not available yet")
        return decision

    def holds(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in hold.items() if key != "resume_token"}
            for hold in self.app.state.holds.values()
        ]

    def ledger_summary(self) -> dict[str, Any]:
        stats = self.app.state.replay_stats
        overheads = stats["overheads"]
        return {
            "request_count": stats["request_count"],
            "spend_inr": round(stats["spend_inr"], 4),
            "action_counts": dict(sorted(stats["action_counts"].items())),
            "overhead_ms": {
                "mean": sum(overheads) / len(overheads) if overheads else None,
                "p95": max(overheads) if overheads else None,
            },
            "economics": {
                "available": False,
                "reason": "Replay does not produce regret, rework, net value, or intervals",
            },
        }

    def lane_c(self) -> dict[str, Any]:
        return {
            "n_pairs": 0,
            "by_axis": {},
            "e_value": {},
            "series": [],
            "notes": ["Replay does not fabricate Lane C observations"],
        }

    def artifact(self, name: str) -> Any:
        return LiveConsoleSource(self.app, artifacts_root=ARTIFACTS).artifact(name)


def pick_scenario(body: dict[str, Any]) -> str:
    explicit = str(body.get("scenario") or "").strip()
    if explicit in SCENARIOS:
        return explicit
    text = " ".join(str(m.get("content") or "") for m in body.get("messages", [])).lower()
    if any(word in text for word in ("prepay", "foreclos", "clause")):
        return "scene1"
    if any(word in text for word in ("email", "forward", "claim")):
        return "held"
    if any(word in text for word in ("reference", "canary", "internal")):
        return "blocked"
    if any(word in text for word in ("branch", "timing", "hours", "open")):
        return "clean"
    return "scene1"


def build_app(*, token_delay_s: float = TOKEN_DELAY_S) -> FastAPI:
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
    app.state.decisions = {}
    app.state.console_hub = ConsoleHub()
    app.state.console_source = ReplayConsoleSource(app)
    app.state.request_ids = count(1)
    app.state.replay_stats = {
        "request_count": 0,
        "spend_inr": 0.0,
        "action_counts": {},
        "overheads": [],
    }
    app.include_router(console_router)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "replay": True,
            "note": "scripts/replay_console.py -- no model, no calibrator, no retrieval",
            "policy_version": "banking-v3@sha256:replayreplayrepl",
            "risk_engine": {
                "engine": "replay",
                "calibrated_defects": ["contradicted", "ungrounded"],
            },
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
        return {"holds": app.state.console_source.holds()}

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
            return JSONResponse({"error": {"message": "approval requires the resume token"}}, 409)
        app.state.holds.pop(hold_id, None)
        return {"hold_id": hold_id, "state": "approved"}

    @app.post("/v1/holds/{hold_id}/reject")
    async def reject(hold_id: str) -> Any:
        if app.state.holds.pop(hold_id, None) is None:
            return JSONResponse({"error": {"message": "no pending hold with that id"}}, 404)
        return {"hold_id": hold_id, "state": "rejected"}

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = pick_scenario(body if isinstance(body, dict) else {})
        scenario = SCENARIOS[name]
        request_id = f"req_replay_{next(app.state.request_ids):04d}"
        decision_event = {
            **scenario["decision"],
            "decision_id": f"{scenario['decision']['decision_id']}_{request_id}",
        }
        request_holds = [
            {**hold, "hold_id": f"{hold['hold_id']}_{request_id}"} for hold in scenario["holds"]
        ]
        request_scenario = {
            **scenario,
            "decision": decision_event,
            "holds": request_holds,
        }
        app.state.decisions[decision_event["decision_id"]] = {
            **decision_event,
            "request_id": request_id,
            "loss_table": loss_table(
                decision_event["action"],
                decision_event["chosen_loss"],
                decision_event["runner_up"],
                decision_event["margin"],
            ),
            "probs": {signal_name: prob for signal_name, prob in scenario["signals"]},
            "why": [
                "Replay scenario uses fixed calibrated probabilities",
                f"{decision_event['action']} has the lowest available expected loss",
            ],
            "policy_version": "banking-v3@replay",
            "calib_version": "calib-replay-v1",
            "probe_version": "probe-replay-v1",
            "inputs_digest": f"replay:{name}",
            "latency_ms": 15.0,
        }

        for hold in request_holds:
            app.state.holds[hold["hold_id"]] = {
                **hold,
                "request_id": request_id,
                "payload": {
                    "name": hold.get("tool"),
                    "response": decision_event["counterfactual"],
                },
                "evidence": ["retrieved_untrusted content"],
                "flagged_span": "retrieved_untrusted",
                "state": "pending",
                "created_ts": 1_700_000_000.0,
                "sla_deadline_ts": None,
                "expired": False,
                "resume_token": f"replay-token-{request_id}-{hold['hold_id']}",
            }

        stats = app.state.replay_stats
        stats["request_count"] += 1
        stats["spend_inr"] += 0.04
        action_counts = stats["action_counts"]
        action_counts[decision_event["action"]] = action_counts.get(decision_event["action"], 0) + 1
        stats["overheads"].append(15.0)

        return StreamingResponse(
            _stream(request_scenario, request_id),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "x-interlock-replay": name,
                "x-interlock-request-id": request_id,
            },
        )

    async def _stream(scenario: dict[str, Any], request_id: str) -> AsyncIterator[str]:
        app.state.console_hub.publish("interlock.stakes", scenario["stakes"], request_id=request_id)
        yield sse("interlock.stakes", scenario["stakes"])
        await asyncio.sleep(min(0.05, token_delay_s))

        for signal_name, prob in scenario["signals"]:
            signal = {
                "sentence_idx": scenario["decision"]["sentence_idx"],
                "name": signal_name,
                "prob": prob,
            }
            app.state.console_hub.publish("interlock.signal", signal, request_id=request_id)
            yield sse("interlock.signal", signal)
            await asyncio.sleep(min(0.04, token_delay_s))

        # A blocked response never reaches the customer, so no content is streamed --
        # the console must handle a stream that carries decisions and no text at all.
        if scenario["decision"]["action"] not in {"L4_hold", "L5_block"}:
            for raw in load_fixture(scenario["fixture"]):
                if raw == "[DONE]":
                    break
                yield sse(None, raw)
                await asyncio.sleep(token_delay_s)

        decision = dict(scenario["decision"])
        app.state.console_hub.publish("interlock.decision", decision, request_id=request_id)
        yield sse("interlock.decision", decision)

        for hold in scenario["holds"]:
            stream_hold = {
                **hold,
                "resume_token": app.state.holds[hold["hold_id"]]["resume_token"],
            }
            app.state.console_hub.publish("interlock.hold", stream_hold, request_id=request_id)
            yield sse("interlock.hold", stream_hold)

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
    print('         -d \'{"scenario":"scene1","messages":[],"stream":true}\'')
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
