"""The gateway — an OpenAI-compatible proxy that any SDK can point ``base_url`` at.

D1-A1 scope: the spine. A request goes in, tokens come back byte-for-byte, and
Interlock metadata rides alongside on named SSE events. Lane A (D1-A2), the commit gate
(D2-A2) and the tool interlock (D3-A1) attach to the seams left here.

Two properties this file exists to protect:

* **Never drop a token.** Whatever the upstream sent, the client gets — including
  chunks we could not parse, which are forwarded rather than swallowed.
* **Never buffer L0 traffic.** ``X-Accel-Buffering: no`` and an unbuffered response, so
  a reverse proxy cannot silently reintroduce the latency the architecture removed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from interlock.core.errors import ProviderError, UpstreamError
from interlock.core.ids import new_request_id, new_stakes_id, new_trace_id
from interlock.core.policy import load_policy
from interlock.core.sse import (
    EVENT_STAKES,
    StakesEvent,
    StreamOptions,
    format_data,
    format_done,
    format_event,
)
from interlock.core.types import Stakes
from interlock.gateway.config import Settings, load_settings
from interlock.gateway.providers import Provider, build_providers
from interlock.risk.stub import StubRiskEngine

__all__ = ["create_app"]

#: Streaming responses must not be buffered by anything between us and the client.
#: nginx and several PaaS proxies buffer SSE by default, which turns a working commit
#: gate into a demo that appears to freeze and then dump the whole answer at once.
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

        app.state.settings = settings
        app.state.client = client
        app.state.policy = policy
        app.state.providers = build_providers(settings, client)
        app.state.risk_engine = StubRiskEngine(policy=policy)
        try:
            yield
        finally:
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
            "tiers": {
                name: f"{tier.provider}:{tier.model}"
                for name, tier in app.state.settings.tiers.items()
            },
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        """Advertise the tiers, so an SDK's model list is not empty."""
        tiers = app.state.settings.tiers
        return {
            "object": "list",
            "data": [
                {"id": tier.model, "object": "model", "owned_by": tier.provider}
                for tier in tiers.values()
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        x_interlock_force: str | None = Header(default=None),
        x_interlock_events: str | None = Header(default=None),
    ) -> Any:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error_response("invalid JSON body", status=400, code="invalid_request_error")
        if not isinstance(body, dict) or "messages" not in body:
            return _error_response(
                "'messages' is required", status=400, code="invalid_request_error"
            )

        request_id = new_request_id()
        trace_id = new_trace_id()
        engine: StubRiskEngine = app.state.risk_engine
        engine.arm(request_id, x_interlock_force)

        # Lane A lands here at D1-A2. Until then the stakes estimate is derived from the
        # policy default so the wire format and the shared-estimate id are already real.
        stakes, tier = _provisional_stakes(app.state.policy, body)
        stakes_id = new_stakes_id()

        provider, model = _select_provider(app.state, body, tier)
        upstream_body = {**body, "model": model}

        if not body.get("stream", False):
            try:
                result = await provider.complete(upstream_body)
            except (UpstreamError, ProviderError) as exc:
                engine.disarm(request_id)
                return _error_from_exception(exc)
            engine.disarm(request_id)
            return JSONResponse(
                result,
                headers={"x-interlock-request-id": request_id, "x-interlock-trace-id": trace_id},
            )

        options = _stream_options(x_interlock_events)
        generator = _stream_response(
            app=app,
            provider=provider,
            body=upstream_body,
            request_id=request_id,
            stakes=stakes,
            stakes_id=stakes_id,
            model=model,
            options=options,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                **_STREAM_HEADERS,
                "x-interlock-request-id": request_id,
                "x-interlock-trace-id": trace_id,
            },
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
    stakes: Stakes,
    stakes_id: str,
    model: str,
    options: StreamOptions,
) -> AsyncIterator[str]:
    """Frame the upstream stream, with Interlock events alongside.

    The commit gate replaces the straight passthrough at D2-A2. The seam is deliberate:
    everything here already knows the stakes, the mode and the request id, so the gate
    slots in without the surrounding code changing.
    """
    engine: StubRiskEngine = app.state.risk_engine
    policy = app.state.policy
    buffered = stakes.impact_inr >= policy.thresholds.buffer_above_impact_inr

    if options.allows(EVENT_STAKES):
        yield format_event(
            EVENT_STAKES,
            StakesEvent(
                impact_inr=stakes.impact_inr,
                reversibility=stakes.reversibility,
                domain=stakes.domain,
                mode="buffered" if buffered else "unbuffered",
                stakes_id=stakes_id,
                route_reason="stakes_high" if buffered else "stakes_low",
                model_served=model,
            ),
        )

    try:
        async for event in provider.stream(body):
            if event.is_done:
                break
            # Byte-for-byte. Re-serialising the provider's JSON is a needless way to
            # break a client's parser, and an unparseable chunk is still the customer's
            # tokens -- forward it rather than swallowing it.
            yield format_data(event.raw)
    except (UpstreamError, ProviderError) as exc:
        # Mid-stream failure: the client has already received a 200 and some tokens, so
        # the only honest thing left is an in-band error chunk followed by [DONE].
        yield format_data(_error_body(str(exc), code="upstream_error"))
        yield format_done()
        engine.disarm(request_id)
        return

    # No decision event here. Decisions are per-sentence and come from the commit gate
    # (D2-A2); emitting a synthetic one would put a decision in the trace that nothing
    # actually made, which is the opposite of what this system is for.

    yield format_done()
    engine.disarm(request_id)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _provisional_stakes(policy: Any, body: dict[str, Any]) -> tuple[Stakes, str]:
    """A placeholder stakes estimate until Lane A lands at D1-A2.

    Deliberately the policy default rather than a guess: an unclassified request must
    not be treated as expensive, or every request holds for a human.
    """
    domain = policy.domain("general")
    stakes = Stakes(
        impact_inr=domain.impact_inr,
        reversibility=domain.reversibility,
        domain="general",
        confidence=0.2,
        rationale=["provisional: Lane A stakes model lands at D1-A2"],
    )
    tier = (
        "strong"
        if stakes.impact_inr >= policy.thresholds.strong_model_above_impact_inr
        else "cheap"
    )
    return stakes, tier


def _select_provider(state: Any, body: dict[str, Any], tier_name: str) -> tuple[Provider, str]:
    """Pick the upstream.

    An explicitly requested model is honoured — we are a proxy, not a gatekeeper of
    which model a client may ask for. Otherwise the tier chosen from the stakes estimate
    decides, which is the routing half of Contribution 1.
    """
    providers: dict[str, Provider] = state.providers
    settings: Settings = state.settings
    tier = settings.tiers[tier_name]

    requested = body.get("model")
    if requested:
        for candidate in settings.tiers.values():
            if candidate.model == requested:
                return providers[candidate.provider], candidate.model
        # An unknown model name still routes to the default provider, so a client that
        # asks for 'gpt-4o' against a local deployment gets an answer rather than a 404.
        return providers[tier.provider], str(requested)
    return providers[tier.provider], tier.model


def _error_body(message: str, *, code: str, kind: str = "interlock_error") -> str:
    return json.dumps(
        {"error": {"message": message, "type": kind, "code": code}},
        separators=(",", ":"),
    )


def _error_response(message: str, *, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        json.loads(_error_body(message, code=code, kind="invalid_request_error")),
        status_code=status,
    )


def _error_from_exception(exc: Exception) -> JSONResponse:
    status = getattr(exc, "status", None) or 502
    return JSONResponse(
        json.loads(_error_body(str(exc), code="upstream_error")),
        status_code=int(status),
    )


app = create_app()
