"""The gateway — an OpenAI-compatible proxy that any SDK can point ``base_url`` at.

The request lifecycle, in the order it happens:

1. Lane A runs pre-flight under a hard deadline (injection, PII, canary, stakes, route).
2. Deterministic pre-block rules short-circuit **before** the upstream is called — a
   canary in the outbound prompt must never reach a provider at all.
3. The upstream is opened on the tier Lane A chose *from the same stakes estimate the
   risk engine will price with*.
4. Tokens stream back through the commit gate: byte-for-byte when unbuffered,
   one sentence behind when the stakes justify it.
5. The whole request commits to the ledger in one transaction, off the token path.

Two properties this file exists to protect:

* **Never drop a token.** Whatever the upstream sent, the client gets — including chunks
  we could not parse, which are forwarded rather than swallowed.
* **Never buffer L0 traffic.** ``X-Accel-Buffering: no`` and an unbuffered response, so
  a reverse proxy cannot silently reintroduce the latency the architecture removed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from interlock.core.clock import monotonic_ms
from interlock.core.errors import ProviderError, UpstreamError
from interlock.core.ids import new_hold_id, new_request_id, new_trace_id
from interlock.core.policy import load_policy
from interlock.core.sse import (
    EVENT_DECISION,
    EVENT_HOLD,
    EVENT_STAKES,
    DecisionEvent,
    HoldEvent,
    StakesEvent,
    StreamOptions,
    format_data,
    format_done,
    format_event,
)
from interlock.gate.repair import SentenceRepairer
from interlock.gate.sentence_gate import CommitGate, Emission
from interlock.gateway.config import Settings, load_settings
from interlock.gateway.lane_a import LaneA, PreflightResult
from interlock.gateway.providers import Provider, build_providers
from interlock.interlock_tools.holds import ToolInterlock
from interlock.interlock_tools.streaming import ToolCallAccumulator
from interlock.ledger.writer import Ledger, RequestBatch
from interlock.retrieval.embedder import load_embedder
from interlock.retrieval.retriever import NullRetriever, Retriever
from interlock.risk.calibration import MultiDefectCalibrator
from interlock.risk.engine import RealRiskEngine, load_conformal
from interlock.risk.stub import StubRiskEngine
from interlock.signals.base import PreflightContext
from interlock.signals.canary import CanaryDetector, CanaryRegistry
from interlock.signals.injection import InjectionDetector, PatternInjectionBackend
from interlock.signals.pii import PIIDetector

_log = logging.getLogger("interlock.gateway")

__all__ = ["create_app"]

#: Streaming responses must not be buffered by anything between us and the client.
#: nginx and several PaaS proxies buffer SSE by default, which turns a working commit
#: gate into a demo that appears to freeze and then dumps the whole answer at once.
_STREAM_HEADERS = {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    "connection": "keep-alive",
    "x-accel-buffering": "no",
}


def _stream_options(header_value: str | None) -> StreamOptions:
    """Honour ``X-Interlock-Events: off`` (see the caveat in ``core/sse.py``)."""
    if header_value and header_value.strip().lower() in {"off", "0", "false", "none"}:
        return StreamOptions(emit_interlock_events=False)
    return StreamOptions()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One pooled client for every upstream. Connection reuse is the difference
        # between adding ~1 ms and adding a TLS handshake to every request.
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.upstream_connect_timeout_s,
                read=settings.upstream_read_timeout_s,
                write=settings.upstream_read_timeout_s,
                pool=settings.upstream_connect_timeout_s,
            ),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
        # An invalid policy must stop the process, not be discovered mid-demo.
        policy = load_policy(settings.policy_path)

        canaries = CanaryRegistry()
        canaries.mint(settings.tenant_id)

        ledger = Ledger(db_path=settings.db_path, store_prompts=settings.store_prompts)
        await ledger.start()

        app.state.settings = settings
        app.state.client = client
        app.state.policy = policy
        app.state.canaries = canaries
        app.state.ledger = ledger
        app.state.providers = build_providers(settings, client)
        app.state.risk_engine = _build_risk_engine(settings, policy, canaries)
        app.state.retriever = _open_retriever(settings)
        app.state.tool_interlock = ToolInterlock(policy=policy, ledger=ledger)
        app.state.lane_a = LaneA(
            policy=policy,
            detectors=[
                InjectionDetector(backend=PatternInjectionBackend()),
                PIIDetector(),
                CanaryDetector(registry=canaries),
            ],
            deadline_ms=settings.lane_a_deadline_ms,
            retriever=app.state.retriever,
        )
        try:
            yield
        finally:
            app.state.retriever.close()
            await ledger.stop()
            await client.aclose()

    app = FastAPI(title="Interlock gateway", version="0.1.0", lifespan=lifespan)

    # ----------------------------------------------------------------- routes #

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "gateway",
            "policy_version": app.state.policy.policy_version,
            "risk_engine": app.state.risk_engine.health(),
            "providers": sorted(app.state.providers),
            "ledger": app.state.ledger.stats(),
            "lane_a_deadline_ms": app.state.settings.lane_a_deadline_ms,
            "retrieval": _retrieval_health(app.state.retriever),
            "tiers": {
                name: f"{tier.provider}:{tier.model}"
                for name, tier in app.state.settings.tiers.items()
            },
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        """Advertise the tiers, so an SDK's model list is not empty."""
        return {
            "object": "list",
            "data": [
                {"id": tier.model, "object": "model", "owned_by": tier.provider}
                for tier in app.state.settings.tiers.values()
            ],
        }

    @app.get("/v1/holds")
    async def holds() -> dict[str, Any]:
        """Pending review cards. Read straight from the durable table, which is what
        makes them survive a restart (F6/F7)."""
        return {"holds": app.state.tool_interlock.pending_cards()}

    @app.post("/v1/holds/{hold_id}/approve")
    async def approve_hold(hold_id: str, request: Request) -> Any:
        """Release a frozen tool call. Requires the resume token.

        The hold id travels in console URLs and log lines; the token does not. Knowing
        that a hold exists must not be the same as being able to release it.
        """
        return await _resolve_hold(app, hold_id, request, state="approved")

    @app.post("/v1/holds/{hold_id}/reject")
    async def reject_hold(hold_id: str, request: Request) -> Any:
        """Cancel a frozen tool call. Deliberately does NOT require the token: a
        reviewer who cannot produce a secret must still be able to stop a pending
        irreversible action."""
        return await _resolve_hold(app, hold_id, request, state="rejected")

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        x_interlock_force: str | None = Header(default=None),
        x_interlock_events: str | None = Header(default=None),
        x_interlock_role: str | None = Header(default=None),
    ) -> Any:
        started_ms = monotonic_ms()
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error_response("invalid JSON body", status=400, code="invalid_request_error")
        if not isinstance(body, dict) or "messages" not in body:
            return _error_response(
                "'messages' is required", status=400, code="invalid_request_error"
            )

        settings: Settings = app.state.settings
        ledger: Ledger = app.state.ledger
        request_id = new_request_id()
        trace_id = new_trace_id()
        app.state.risk_engine.arm(request_id, x_interlock_force)

        # ---- Lane A: the only synchronous work before the model is called ----
        preflight_ctx = PreflightContext(
            request_id=request_id,
            tenant_id=settings.tenant_id,
            messages=list(body.get("messages") or []),
            retrieved=_fragments_from_body(body),
            tools=list(body.get("tools") or []),
            user_role=(x_interlock_role or "customer").strip().lower(),
        )
        lane: PreflightResult = await app.state.lane_a.run(preflight_ctx)

        # ---- Deterministic pre-block, before any provider sees the prompt ----
        if lane.hard_rules:
            rule = lane.hard_rules[0]
            ledger.record(
                _batch_from(
                    request_id,
                    trace_id,
                    settings,
                    lane,
                    body,
                    overhead_ms=monotonic_ms() - started_ms,
                    finish_reason=f"blocked:{rule.name}",
                )
            )
            app.state.risk_engine.disarm(request_id)
            return _error_response(
                f"blocked by a deterministic rule: {rule.reason}",
                status=403,
                code=rule.name,
            )

        provider, model = _select_provider(app.state, body, lane.tier)
        upstream_body = {**body, "model": model}
        headers = {
            "x-interlock-request-id": request_id,
            "x-interlock-trace-id": trace_id,
            "x-interlock-stakes-id": lane.stakes_id,
            "x-interlock-route-reason": lane.route_reason,
        }

        if not body.get("stream", False):
            upstream_started = monotonic_ms()
            try:
                result = await provider.complete(upstream_body)
            except (UpstreamError, ProviderError) as exc:
                app.state.risk_engine.disarm(request_id)
                return _error_from_exception(exc)
            upstream_ms = monotonic_ms() - upstream_started
            ledger.record(
                _batch_from(
                    request_id,
                    trace_id,
                    settings,
                    lane,
                    body,
                    model_served=model,
                    upstream_ms=upstream_ms,
                    overhead_ms=(monotonic_ms() - started_ms) - upstream_ms,
                    completion_tokens=_usage(result, "completion_tokens"),
                    prompt_tokens=_usage(result, "prompt_tokens"),
                )
            )
            app.state.risk_engine.disarm(request_id)
            return JSONResponse(result, headers=headers)

        generator = _stream_response(
            app=app,
            provider=provider,
            body=upstream_body,
            request_id=request_id,
            trace_id=trace_id,
            lane=lane,
            model=model,
            options=_stream_options(x_interlock_events),
            started_ms=started_ms,
            original_body=body,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={**_STREAM_HEADERS, **headers},
        )

    return app


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #


async def _stream_response(
    *,
    app: FastAPI,
    provider: Provider,
    body: dict[str, Any],
    request_id: str,
    trace_id: str,
    lane: PreflightResult,
    model: str,
    options: StreamOptions,
    started_ms: float,
    original_body: dict[str, Any],
) -> AsyncIterator[str]:
    """Frame the upstream stream through the commit gate.

    Low-stakes traffic passes byte-for-byte; high-stakes traffic streams one sentence
    behind so a bad sentence can be repaired before anyone reads it. Which of the two
    happens was decided by Lane A, from the same stakes estimate that chose the model.
    """
    settings: Settings = app.state.settings
    ledger: Ledger = app.state.ledger
    engine: StubRiskEngine = app.state.risk_engine

    gate = CommitGate(
        risk_engine=engine,
        stakes=lane.stakes,
        request_id=request_id,
        mode=lane.mode,
        retrieved=lane.fragments,
        question=_last_user_message(original_body),
        watchdog_s=settings.sentence_watchdog_s,
        evaluate_deadline_ms=settings.observe_deadline_ms,
        repair=SentenceRepairer(
            provider=provider,
            model=model,
            risk_engine=engine,
            stakes=lane.stakes,
            request_id=request_id,
            question=_last_user_message(original_body),
            retrieved=lane.fragments,
        ),
    )
    # Tool calls stream one *call* behind, for the same reason text streams one
    # sentence behind: there is no moment during the stream when a half-assembled
    # call could be judged. `{"to":` is not an argument.
    tool_calls = ToolCallAccumulator()
    interlock: ToolInterlock = app.state.tool_interlock
    interlock.observe(request_id, lane.fragments)

    chunk_id = f"chatcmpl-{request_id}"

    if options.allows(EVENT_STAKES):
        yield format_event(
            EVENT_STAKES,
            StakesEvent(
                impact_inr=lane.stakes.impact_inr,
                reversibility=lane.stakes.reversibility,
                domain=lane.stakes.domain,
                mode=lane.mode,
                stakes_id=lane.stakes_id,
                route_reason=lane.route_reason,
                model_served=model,
            ),
        )

    upstream_started = monotonic_ms()
    ttft_ms = 0.0
    completion_chars = 0
    finish_reason: str | None = None
    completed = False

    # The whole stream sits inside try/finally so the ledger row is written even when
    # the client hangs up mid-stream -- a closed tab, a proxy timeout, a cancelled
    # request. Those are common, they still cost upstream tokens, and a request that
    # incurs cost without leaving a trace makes the spend numbers quietly wrong. The
    # `finally` also runs on GeneratorExit, which is how ASGI cancels a disconnected
    # stream, and `record` is non-blocking so it is safe to call from there.
    try:
        try:
            async for event in provider.stream(body):
                if event.is_done:
                    break
                if ttft_ms == 0.0:
                    ttft_ms = monotonic_ms() - upstream_started
                completion_chars += len(event.text)
                if event.data:
                    for choice in event.data.get("choices", []):
                        finish_reason = choice.get("finish_reason") or finish_reason
                # Withheld, not dropped: replayed below if the interlock clears them.
                if tool_calls.absorb(event.data):
                    continue
                # A chunk we could not parse carries text we cannot segment, verify or
                # repair. Dropping it would violate "never drop a token", so it is
                # forwarded raw and unverified -- an honest, narrow gap, and better than
                # silently losing the customer's data. Valid chunks with no content
                # (a role-only opener) carry nothing and are simply consumed.
                if gate.buffered and event.data is None and event.raw:
                    yield format_data(event.raw)
                    continue
                for frame in _render(
                    await gate.push(event.text, raw=event.raw), options, chunk_id, model, lane
                ):
                    yield frame
        except (UpstreamError, ProviderError) as exc:
            # Mid-stream failure: the client already has a 200 and some tokens, so the
            # only honest thing left is an in-band error chunk followed by [DONE].
            finish_reason = "upstream_error"
            yield format_data(_error_body(str(exc), code="upstream_error"))

        # Drain the gate: the last buffered sentence is still holding, and abandoning it
        # would silently truncate the answer.
        for frame in _render(await gate.finish(), options, chunk_id, model, lane):
            yield frame

        # ---- the tool-call interlock -------------------------------------- #
        #
        # Every assembled call is judged before any of them is released. A turn that
        # requests two calls is one decision: releasing the safe one while freezing the
        # other leaves the client having executed half a plan, which is a state no
        # agent loop is written to recover from.
        released: list[dict[str, Any]] = []
        for call in tool_calls.assemble():
            decision, held = await interlock.check(call, lane.fragments, request_id=request_id)
            if held is not None:
                finish_reason = "tool_call_held"
                if options.allows(EVENT_HOLD):
                    yield format_event(
                        EVENT_HOLD,
                        HoldEvent(
                            hold_id=held.hold_id,
                            kind="tool_call",
                            reason=decision.reason,
                            tool=call.name,
                        ),
                    )
                released = []
                break
            released.append(call.call_id)

        if tool_calls.saw_any and finish_reason != "tool_call_held":
            # Cleared. Replay the assembled calls as a single chunk -- the client sees
            # one complete tool_calls message instead of the fragments we absorbed.
            yield format_data(
                json.dumps(
                    {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"tool_calls": tool_calls.raw_entries()},
                                "finish_reason": "tool_calls",
                            }
                        ],
                    },
                    separators=(",", ":"),
                )
            )

        # Any sentence the gate withheld for human review becomes a durable hold, so it
        # survives a restart (F6/F7). Awaited, not queued -- see ledger/writer.py.
        for decision in gate.decisions:
            if decision.action in {"L3_reroute", "L4_hold"}:
                hold_id = new_hold_id()
                await ledger.persist_hold(
                    hold_id=hold_id,
                    request_id=request_id,
                    kind="response",
                    payload={"action": decision.action, "decision_id": decision.decision_id},
                    evidence=decision.why,
                    reason=decision.hard_rule or decision.action,
                )
                if options.allows(EVENT_HOLD):
                    yield format_event(
                        EVENT_HOLD,
                        HoldEvent(
                            hold_id=hold_id,
                            kind="response",
                            reason=decision.hard_rule or decision.action,
                        ),
                    )

        # A degraded Lane A is reported to the console rather than hidden: the reviewer
        # needs to know the answer was checked with fewer detectors than usual.
        if lane.degraded and options.allows(EVENT_DECISION):
            yield format_event(
                EVENT_DECISION,
                DecisionEvent(
                    decision_id="dec_degraded",
                    sentence_idx=-1,
                    action="L0_pass",
                    chosen_loss=0.0,
                    degraded=True,
                ),
            )

        yield format_done()
        completed = True
    finally:
        upstream_ms = monotonic_ms() - upstream_started
        ledger.record(
            _batch_from(
                request_id,
                trace_id,
                settings,
                lane,
                original_body,
                model_served=model,
                upstream_ms=upstream_ms,
                overhead_ms=(monotonic_ms() - started_ms) - upstream_ms,
                ttft_ms=ttft_ms,
                completion_tokens=max(1, completion_chars // 4) if completion_chars else 0,
                finish_reason=finish_reason or ("stop" if completed else "client_disconnect"),
                decisions=gate.decisions,
            )
        )
        engine.disarm(request_id)
        # Taint is per-request state on a long-lived process; leaving it would grow
        # without bound and, worse, leak one customer's poisoned upload into the next
        # request that happened to reuse the id.
        interlock.forget(request_id)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _render(
    emissions: list[Emission],
    options: StreamOptions,
    chunk_id: str,
    model: str,
    lane: PreflightResult,
) -> list[str]:
    """Turn the gate's emissions into SSE frames.

    Two shapes, and the distinction is deliberate. A ``raw`` emission is the provider's
    own bytes and is forwarded untouched, which is what keeps unbuffered traffic
    byte-identical. A ``text`` emission is a sentence the gate assembled and may have
    replaced, so it has to be wrapped in a fresh chunk -- it is no longer what the
    provider sent, and pretending otherwise would put words in their mouth.
    """
    frames: list[str] = []
    for emission in emissions:
        if emission.kind == "raw":
            frames.append(format_data(emission.raw))
        elif emission.kind == "text":
            frames.append(format_data(_chunk(chunk_id, model, emission.text)))
        elif emission.kind == "event" and emission.decision is not None:
            if not options.allows(EVENT_DECISION):
                continue
            decision = emission.decision
            frames.append(
                format_event(
                    EVENT_DECISION,
                    DecisionEvent(
                        decision_id=decision.decision_id,
                        sentence_idx=emission.sentence_idx,
                        action=decision.action,
                        chosen_loss=decision.chosen_loss,
                        runner_up=decision.runner_up,
                        margin=decision.margin,
                        # What would have shipped. The console renders it in red beside
                        # what actually did, and it is the line the demo lands on.
                        counterfactual=(
                            emission.original if decision.action != "L0_pass" else None
                        ),
                        hard_rule=decision.hard_rule,
                        degraded=lane.degraded,
                    ),
                )
            )
    return frames


def _chunk(chunk_id: str, model: str, text: str) -> str:
    """Wrap gate-assembled text in an OpenAI-shaped streaming chunk.

    Needed because this text is no longer what the provider sent -- the gate may have
    annotated or replaced it -- so it cannot be forwarded as raw bytes.
    """
    return json.dumps(
        {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _last_user_message(body: dict[str, Any]) -> str:
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _batch_from(
    request_id: str,
    trace_id: str,
    settings: Settings,
    lane: PreflightResult,
    body: dict[str, Any],
    **overrides: Any,
) -> RequestBatch:
    """Fold Lane A's result into the ledger row.

    ``stakes_id`` is written here and read by the router's ``route_reason``: one trace
    row proves the two consumed the same estimate, which is what makes Contribution 1
    checkable rather than asserted.
    """
    batch = RequestBatch(
        request_id=request_id,
        trace_id=trace_id,
        tenant_id=settings.tenant_id,
        model_requested=str(body.get("model") or ""),
        route_reason=lane.route_reason,
        stakes_id=lane.stakes_id,
        stakes_impact_inr=lane.stakes.impact_inr,
        stakes_reversibility=lane.stakes.reversibility,
        stakes_domain=lane.stakes.domain,
        stakes_confidence=lane.stakes.confidence,
        gate_mode=lane.mode,
        lane_a_ms=lane.elapsed_ms,
        degraded=lane.degraded,
        dropped_detectors=lane.dropped,
        signals=lane.signals,
        prompt_text=_flatten_prompt(body),
    )
    for key, value in overrides.items():
        setattr(batch, key, value)
    return batch


def _flatten_prompt(body: dict[str, Any]) -> str:
    return "\n".join(str(m.get("content") or "") for m in body.get("messages") or [])


def _usage(result: dict[str, Any], key: str) -> int:
    usage = result.get("usage")
    return int(usage.get(key, 0)) if isinstance(usage, dict) else 0


async def _resolve_hold(app: FastAPI, hold_id: str, request: Request, *, state: str) -> Any:
    """Shared approve/reject handling."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    ok, why = await app.state.tool_interlock.resolve(
        hold_id,
        state=state,
        resolved_by=str(payload.get("resolved_by") or "operator"),
        resume_token=payload.get("resume_token"),
    )
    if not ok:
        # 404 for "no such hold", 409 for everything else: a wrong token and an
        # already-resolved hold are both "your view of the world is stale", which is
        # a conflict, not a missing resource.
        status = 404 if "no pending hold" in why else 409
        return _error_response(why, status=status, code="hold_not_resolved")
    return {"hold_id": hold_id, "state": state}


def _build_risk_engine(settings: Settings, policy: Any, canaries: Any) -> Any:
    """The D3-B4 swap, in one place.

    The plan budgets "30 minutes of pain" for this and predicts that if the contracts
    were honoured it just works. They were: both engines satisfy the same Protocol, the
    enforcement path calls neither by name, and the only thing that changes is which
    object the gateway constructs.
    """
    if settings.risk_engine == "stub":
        _log.warning(
            "risk_engine=stub -- decisions are driven by the X-Interlock-Force header, "
            "not by detectors. Never serve real traffic in this mode."
        )
        return StubRiskEngine(policy=policy)

    calibrator = None
    calib_version = "uncalibrated"
    path = settings.calibration_dir / "calibrator_per_defect.json"
    if path.exists():
        try:
            calibrator = MultiDefectCalibrator.load(path)
            calib_version = _artefact_version(path)
        except Exception as exc:
            _log.warning("calibrator at %s unusable (%s); decisions will be degraded", path, exc)
    else:
        _log.warning(
            "no calibrator at %s -- every decision will report no defect probabilities "
            "and be marked degraded. Run scripts/calibrate.py.",
            path,
        )

    conformal = load_conformal(settings.calibration_dir / "lambda.json")
    return RealRiskEngine(
        policy=policy,
        calibrator=calibrator,
        conformal=conformal,
        canary_detector=CanaryDetector(registry=canaries),
        conformal_filter=settings.conformal_filter,
        calib_version=calib_version,
    )


def _artefact_version(path: Path) -> str:
    """A short content hash, so a decision records WHICH calibration priced it."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"calib@sha256:{digest}"


def _retrieval_health(retriever: Retriever | NullRetriever) -> dict[str, Any]:
    """Reported, not assumed. "Retrieval is off" must be visible from outside."""
    if isinstance(retriever, NullRetriever):
        return {"available": False, "reason": retriever.reason}
    return {
        "available": True,
        "chunks": len(retriever.index),
        "embedder": retriever.index.meta.get("embedder", "?"),
        "semantic": bool(getattr(retriever.index.embedder, "semantic", False)),
        "corpus_version": retriever.index.meta.get("corpus_version", ""),
        "k": retriever.k,
    }


def _open_retriever(settings: Settings) -> Retriever | NullRetriever:
    """Open the corpus index, or degrade loudly to no retrieval at all.

    A missing index must not stop the gateway -- the proxy is useful in front of a
    caller that does its own retrieval, which is the honest deployment shape. But it
    must not be invisible either: retrieval quietly returning nothing forever is
    exactly what gets noticed the week after a demo, so the reason is logged at
    startup and reported on ``/health``.
    """
    path = settings.corpus_index_path
    if not path.exists():
        _log.warning(
            "no corpus index at %s -- answers will be ungrounded unless the caller "
            "attaches its own context; build one with scripts/build_index.py",
            path,
        )
        return NullRetriever(reason=f"no index at {path}")
    try:
        return Retriever.open(
            path, embedder=load_embedder(settings.embedder), k=settings.retrieval_k
        )
    except Exception as exc:  # a stale index, or sqlite-vec missing
        _log.warning("corpus index at %s unusable (%s) -- continuing without it", path, exc)
        return NullRetriever(reason=str(exc))


def _fragments_from_body(body: dict[str, Any]) -> list[Any]:
    """Read retrieved context the caller attached.

    A demo app that does its own retrieval passes fragments in
    ``extra_body["interlock"]["retrieved"]`` so we can label provenance per chunk.
    Without them we still work; we simply have less to be specific about.
    """
    from interlock.core.types import Fragment

    interlock_block = body.get("interlock")
    if not isinstance(interlock_block, dict):
        return []
    raw = interlock_block.get("retrieved")
    if not isinstance(raw, list):
        return []
    fragments = []
    for item in raw:
        if isinstance(item, dict) and item.get("text"):
            fragments.append(
                Fragment(
                    text=str(item["text"]),
                    provenance=item.get("provenance", "retrieved_untrusted"),
                    doc_id=item.get("doc_id"),
                    domain=item.get("domain"),
                    score=item.get("score"),
                )
            )
    return fragments


#: A client asking Interlock to choose. ``model`` is required by the OpenAI schema,
#: so "let the router decide" needs a name rather than an omission.
ROUTING_SENTINELS = frozenset({"auto", "interlock", "interlock/auto", "interlock-auto"})


def _select_provider(state: Any, body: dict[str, Any], tier_name: str) -> tuple[Provider, str]:
    """Pick the upstream.

    An explicitly requested model is honoured — we are a proxy, not a gatekeeper of
    which model a client may ask for. Otherwise the tier chosen from the stakes estimate
    decides, which is the routing half of Contribution 1.
    """
    providers: dict[str, Provider] = state.providers
    settings: Settings = state.settings
    tier = settings.tiers[tier_name]

    requested = str(body.get("model") or "").strip()
    if requested and requested.lower() not in ROUTING_SENTINELS:
        for candidate in settings.tiers.values():
            if candidate.model == requested:
                return providers[candidate.provider], candidate.model
        # An unknown model name is passed through unchanged. If the upstream does not
        # have it, its 404 is surfaced as-is rather than being silently rewritten to
        # some other model -- a proxy that quietly answers with a different model than
        # the one asked for is a proxy nobody can measure anything against.
        return providers[tier.provider], requested
    # No model, or the routing sentinel: the stakes estimate decides. This is the
    # routing half of Contribution 1, and the sentinel exists because `model` is a
    # required field in the OpenAI schema -- a client cannot ask for routing by
    # omitting it without sending a request its own SDK will reject.
    return providers[tier.provider], tier.model


def _error_body(message: str, *, code: str, kind: str = "interlock_error") -> str:
    return json.dumps(
        {"error": {"message": message, "type": kind, "code": code}},
        separators=(",", ":"),
    )


def _error_response(message: str, *, status: int, code: str) -> JSONResponse:
    kind = "invalid_request_error" if status == 400 else "interlock_error"
    return JSONResponse(json.loads(_error_body(message, code=code, kind=kind)), status_code=status)


def _error_from_exception(exc: Exception) -> JSONResponse:
    status = getattr(exc, "status", None) or 502
    return JSONResponse(
        json.loads(_error_body(str(exc), code="upstream_error")),
        status_code=int(status),
    )


app = create_app()
