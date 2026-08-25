"""A mock observer — Contract 2, with no model weights.

Exists so the gateway's Lane B path can be built and tested before the real observer,
and so the **deadline and degradation paths** can be exercised deterministically. A
circuit breaker that has only ever been tested against a healthy dependency is not a
tested circuit breaker.

Scripting is by request field, so a test, a demo and a chaos run all drive it the same way:

* ``sentence`` containing ``[[HALLUCINATE]]``  -> a high probe score and a contradicted claim
* ``sentence`` containing ``[[SLOW:250]]``     -> sleep 250 ms before answering
* ``sentence`` containing ``[[DEGRADE]]``      -> the in-band degraded response
* otherwise                                    -> low scores and a supported claim

It keeps a real KV-prefix cache **key** set (not a real cache) so ``context_cached``
behaves correctly and the gateway's "send context only on a miss" logic is exercised.

This module is retained after the real observer lands: it stays the fixture the chaos
tests run against.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from fastapi import FastAPI

from interlock.core.observer_api import (
    ClaimVerdict,
    ObserveRequest,
    ObserveResponse,
    ObserverHealth,
    RawSignal,
)

__all__ = ["MOCK_PROBE_VERSION", "create_mock_observer"]

MOCK_PROBE_VERSION = "p_mock_v1"

_SLOW = re.compile(r"\[\[SLOW:(\d+)\]\]")
_HALLUCINATE = "[[HALLUCINATE]]"
_DEGRADE = "[[DEGRADE]]"

#: Matches the real observer's LRU of 64 prefixes, so cache-miss behaviour is realistic.
_CACHE_CAPACITY = 64


class _PrefixCache:
    """Tracks which context keys are warm. Keys only -- there is nothing to cache."""

    def __init__(self, capacity: int = _CACHE_CAPACITY) -> None:
        self._keys: OrderedDict[str, None] = OrderedDict()
        self._capacity = capacity

    def touch(self, key: str) -> bool:
        """Record a use; return whether it was already warm."""
        hit = key in self._keys
        if hit:
            self._keys.move_to_end(key)
        else:
            self._keys[key] = None
            if len(self._keys) > self._capacity:
                self._keys.popitem(last=False)
        return hit

    def clear(self) -> None:
        self._keys.clear()


def create_mock_observer() -> FastAPI:
    """Build the mock observer app. Implements Contract 2 exactly."""
    app = FastAPI(title="Interlock mock observer", version="0.1.0")
    cache = _PrefixCache()
    state = {"requests": 0, "last_p95_ms": 0.0}

    @app.post("/v1/observe", response_model=ObserveResponse)
    async def observe(request: ObserveRequest) -> ObserveResponse:
        state["requests"] = int(state["requests"]) + 1
        sentence = request.sentence

        # Scripted latency, for testing the deadline and the circuit breaker.
        slow = _SLOW.search(sentence)
        if slow:
            await asyncio.sleep(int(slow.group(1)) / 1000.0)

        # Scripted failure. Note it is still a 200 -- the contract never lets the
        # gateway see a 5xx from here.
        if _DEGRADE in sentence:
            return ObserveResponse.degraded_response("scripted degradation")

        cached = cache.touch(request.context_key)
        hallucinating = _HALLUCINATE in sentence

        signals: list[RawSignal] = []
        if "probe" in request.want:
            signals.append(
                RawSignal(
                    name="probe_semantic_entropy",
                    raw=0.71 if hallucinating else 0.08,
                    latency_ms=11.4,
                )
            )
        if "verbal_uncertainty" in request.want:
            # Low verbal uncertainty against high semantic uncertainty is exactly the
            # mismatch the Overconfidence Index is built from.
            signals.append(RawSignal(name="verbal_uncertainty", raw=0.08, latency_ms=0.2))

        claims: list[ClaimVerdict] = []
        if "claims" in request.want:
            span = (0, len(sentence))
            claims.append(
                ClaimVerdict(
                    text=sentence,
                    label="contradicted" if hallucinating else "supported",
                    span=span,
                )
            )
            signals.append(
                RawSignal(
                    name="minicheck_support",
                    raw=0.13 if hallucinating else 0.92,
                    latency_ms=22.0,
                    span=span,
                    evidence=(
                        ["Clause 9.1 states no prepayment charge applies to floating-rate loans."]
                        if hallucinating
                        else []
                    ),
                )
            )

        return ObserveResponse(
            signals=signals,
            claims=claims,
            probe_version=MOCK_PROBE_VERSION,
            context_cached=cached,
            degraded=False,
        )

    @app.get("/health", response_model=ObserverHealth)
    async def health() -> ObserverHealth:
        return ObserverHealth(
            model="mock",
            probe_version=MOCK_PROBE_VERSION,
            gpu=False,
            queue_depth=0,
            p95_ms=float(state["last_p95_ms"]),
            ok=True,
        )

    @app.post("/admin/reset")
    async def reset() -> dict[str, str]:
        """Test-only: forget warm prefixes so cache-miss paths can be re-exercised."""
        cache.clear()
        state["requests"] = 0
        return {"status": "reset"}

    return app


app = create_mock_observer()
