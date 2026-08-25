"""Contract 2 — the Observer HTTP surface (Implementation03 §3).

FROZEN. Stream & Enforcement calls it; Signals & Decisions implements it. The boundary
is HTTP rather than a Python import for three reasons: the observer holds model weights
and must be restartable independently, it is the one component that may want a GPU, and
it must be **mockable** so the entire enforcement path can be built without either.

Two rules that make the gateway's life predictable:

* ``POST /v1/observe`` returns **200 always**, unless the request itself is malformed.
  An internal failure is reported in-band as ``degraded=True`` with an empty ``signals``
  list — never as a 5xx, because the gateway must not have to distinguish "the observer
  is broken" from "the network is broken" on the token path.
* The caller's hard timeout is ``deadline_ms + 30``.

``/v1/judge`` is deliberately a **separate route** so that ``grep`` proves no hot path
touches generative judging (invariant 8). It is Lane C only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from interlock.core.types import Provenance

__all__ = [
    "OBSERVE_TIMEOUT_MARGIN_MS",
    "ClaimLabel",
    "ClaimVerdict",
    "ContextFragment",
    "ObserveRequest",
    "ObserveResponse",
    "ObserverHealth",
    "RawSignal",
    "WantSignal",
]

#: The gateway's hard timeout is ``deadline_ms`` plus this margin.
OBSERVE_TIMEOUT_MARGIN_MS: int = 30

#: Which signal families the caller wants. Lets the governor ask for less under load
#: (SHALLOW drops "claims", PROBE_ONLY keeps only "probe") without a second endpoint.
WantSignal = Literal["probe", "verbal_uncertainty", "claims"]

#: Per-claim groundedness against what was actually retrieved.
ClaimLabel = Literal["supported", "contradicted", "unfindable"]

#: What the gateway asks for when it is not degraded.
_DEFAULT_WANT: list[WantSignal] = ["probe", "claims"]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextFragment(_Wire):
    """A labelled context fragment, sent only when ``context_key`` misses the cache."""

    role: str
    text: str
    provenance: Provenance
    doc_id: str | None = None


class ObserveRequest(_Wire):
    """One forward pass, no generation.

    ``context_key`` is computed by the gateway and is what the observer caches its KV
    prefix under. On a hit, ``context`` is omitted entirely and the sentence costs ~30
    tokens of prefill instead of the whole context — the difference between 200 ms and
    12 ms per sentence.
    """

    request_id: str
    context_key: str  # 'sha256:...' — the KV-prefix cache key
    context: list[ContextFragment] | None = None  # sent ONLY on a cache miss
    question: str
    answer_prefix: str = ""
    sentence: str
    sentence_idx: int
    want: list[WantSignal] = Field(default_factory=lambda: _DEFAULT_WANT.copy())
    deadline_ms: float = 120.0


class RawSignal(_Wire):
    """A detector output, uncalibrated.

    Deliberately **not** ``SignalReading``: the observer has no business emitting a
    probability. Calibration happens on the risk-engine side, where the isotonic
    artefacts and their version live (ADR-002).
    """

    name: str
    raw: float
    latency_ms: float = 0.0
    span: tuple[int, int] | None = None
    evidence: list[str] = Field(default_factory=list)


class ClaimVerdict(_Wire):
    """One claim, its label, and **the span** — which is what L2 repair aims at."""

    text: str
    label: ClaimLabel
    span: tuple[int, int] | None = None


class ObserveResponse(_Wire):
    """Always 200. ``degraded=True`` with empty ``signals`` is how failure is reported."""

    signals: list[RawSignal] = Field(default_factory=list)
    claims: list[ClaimVerdict] = Field(default_factory=list)
    probe_version: str = ""
    context_cached: bool = False
    degraded: bool = False
    degraded_reason: str | None = None

    @classmethod
    def degraded_response(cls, reason: str) -> ObserveResponse:
        """The only failure shape the gateway ever has to handle."""
        return cls(degraded=True, degraded_reason=reason)


class ObserverHealth(_Wire):
    """``GET /health`` — the governor polls this every 2 s to decide degradation state."""

    model: str
    probe_version: str
    gpu: bool
    queue_depth: int = 0
    p95_ms: float = 0.0
    ok: bool = True
