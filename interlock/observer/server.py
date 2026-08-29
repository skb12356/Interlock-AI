"""Production HTTP observer service for the frozen Contract 2 boundary.

The service owns probe/verifier weights and is independently restartable. CPU-heavy
inference runs off the event loop; failures become a 200 degraded response so the
gateway can apply its fail-open/fail-closed governor policy.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from interlock.core.observer_api import (
    ClaimVerdict,
    ObserveRequest,
    ObserveResponse,
    ObserverHealth,
    RawSignal,
)
from interlock.core.types import Fragment
from interlock.signals.probe_signal import ProbeSignal

__all__ = ["app", "create_observer"]


@dataclass
class ObserverRuntime:
    probe: ProbeSignal
    verifier: Any | None = None
    context_capacity: int = 64
    contexts: OrderedDict[str, list[Fragment]] = field(default_factory=OrderedDict)
    requests: int = 0
    failures: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def context_for(self, request: ObserveRequest) -> tuple[list[Fragment], bool]:
        cached = self.contexts.get(request.context_key)
        if cached is not None:
            self.contexts.move_to_end(request.context_key)
            return cached, True
        fragments = [
            Fragment(
                text=item.text,
                provenance=item.provenance,
                role=item.role,
                doc_id=item.doc_id,
            )
            for item in (request.context or [])
        ]
        if fragments and self.context_capacity > 0:
            self.contexts[request.context_key] = fragments
            if len(self.contexts) > self.context_capacity:
                self.contexts.popitem(last=False)
        return fragments, False


def create_observer() -> FastAPI:
    probe = ProbeSignal.load(
        Path(os.environ.get("INTERLOCK_PROBE_PATH", "artifacts/probes/probe.json")),
        model_name=os.environ.get("INTERLOCK_OBSERVER_MODEL"),
    )
    verifier: Any | None = None
    if os.environ.get("INTERLOCK_VERIFIER", "0").lower() in {"1", "true", "yes"}:
        from interlock.observer.verifier import DEFAULT_VERIFIER, ClaimVerifier

        verifier_model = os.environ.get("INTERLOCK_VERIFIER_MODEL") or DEFAULT_VERIFIER
        verifier = ClaimVerifier(verifier_model)
    runtime = ObserverRuntime(probe=probe, verifier=verifier)
    service = FastAPI(title="Interlock observer", version="0.1.0")

    @service.post("/v1/observe", response_model=ObserveResponse)
    async def observe(request: ObserveRequest) -> ObserveResponse:
        runtime.requests += 1
        context, cached = runtime.context_for(request)
        started = time.perf_counter()
        try:
            signals: list[RawSignal] = []
            if "probe" in request.want:
                value = await asyncio.to_thread(runtime.probe.score, request.sentence, context)
                if value is not None:
                    signals.append(RawSignal(name="probe_semantic_entropy", raw=value))

            claims: list[ClaimVerdict] = []
            if "claims" in request.want and runtime.verifier is not None:
                verdict = await asyncio.to_thread(runtime.verifier.verify, request.sentence, context)
                claims = [
                    ClaimVerdict(text=item.text, label=item.label if item.label != "unjudged" else "unfindable", span=item.span)
                    for item in verdict.claims
                ]
                for item in verdict.claims:
                    if item.support is not None:
                        signals.append(
                            RawSignal(
                                name="minicheck_support",
                                raw=item.support,
                                span=item.span,
                                evidence=[item.evidence] if item.evidence else [],
                            )
                        )
            return ObserveResponse(
                signals=signals,
                claims=claims,
                probe_version=runtime.probe.version,
                context_cached=cached,
            )
        except Exception as exc:
            runtime.failures += 1
            return ObserveResponse.degraded_response(f"observer inference failed: {type(exc).__name__}")
        finally:
            runtime.latencies_ms.append((time.perf_counter() - started) * 1000.0)
            del runtime.latencies_ms[:-200]

    @service.get("/health", response_model=ObserverHealth)
    async def health() -> ObserverHealth:
        verifier_health = runtime.verifier.health() if runtime.verifier is not None else {}
        return ObserverHealth(
            model=str(
                getattr(runtime.probe.encoder, "model_name", None)
                or verifier_health.get("model")
                or "unavailable"
            ),
            probe_version=runtime.probe.version,
            gpu=False,
            queue_depth=0,
            p95_ms=(
                sorted(runtime.latencies_ms)[
                    min(len(runtime.latencies_ms) - 1, round(0.95 * (len(runtime.latencies_ms) - 1)))
                ]
                if runtime.latencies_ms
                else 0.0
            ),
            ok=True,
        )

    return service


app = create_observer()
