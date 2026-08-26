"""Lane A — pre-flight, on the user's critical path.

The only synchronous work Interlock does before the model is called. Everything here is
racing a hard deadline, and the design rule that follows from that is unusual enough to
state plainly:

    **A detector that misses the deadline is dropped, not awaited.**

Not "logged as slow", not "waited for a bit longer". Its task is cancelled and its
absence is recorded as a signal with ``prob=None``, because *a missing signal is
information*: "we did not check" is a different claim from "we checked and found
nothing", and the console must be able to tell a reviewer which one happened. A lane
that silently waits for a slow detector is a lane that has no deadline at all.

Retrieval is the third inline step, and it runs **first**, because the stakes model
prices from what was actually retrieved rather than from what the question claimed to
be about -- the part an attacker controls. It has its own sub-budget and fails open: a
retrieval that misses its deadline yields an answer with less grounding, and that
ungroundedness is exactly what Lane B is there to catch. Blocking the request instead
would trade a soft failure for a hard one.

Two things deliberately do **not** race:

* **Stakes.** It is a deterministic sub-millisecond scorer, and without it there is no
  estimate to route on or price with — dropping it would leave the request with no
  budget at all rather than a degraded one. It runs inline.
* **The router.** It consumes the `Stakes` object the risk engine will also consume.
  That sharing is Contribution 1, and it must be provable from one trace, so it happens
  in the same place and records the same ``stakes_id``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from interlock.core.clock import Deadline
from interlock.core.ids import new_stakes_id
from interlock.core.policy import Policy
from interlock.core.types import Fragment, GateMode, SignalReading, Stakes
from interlock.gateway.router import Router
from interlock.risk.objective import HardRule
from interlock.signals.base import Detector, DetectorOutcome, PreflightContext
from interlock.signals.stakes import StakesModel

__all__ = ["LaneA", "PreflightResult", "RetrievalPort"]


class RetrievalPort(Protocol):
    """The slice of ``Retriever`` Lane A depends on -- kept narrow so the gateway can
    be handed a stub, and so a caller-supplied RAG stack is a drop-in replacement."""

    async def retrieve(self, question: str, **kwargs: Any) -> Any: ...


@dataclass(slots=True)
class PreflightResult:
    """Everything Lane A decided, and everything it could not."""

    stakes: Stakes
    stakes_id: str
    #: 'cheap' | 'strong' -- chosen from the same estimate the risk engine will price with.
    tier: str
    route_reason: str
    mode: GateMode
    signals: list[SignalReading] = field(default_factory=list)
    hard_rules: list[HardRule] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    #: Detectors cancelled for missing the deadline. Never empty silently.
    dropped: list[str] = field(default_factory=list)
    #: Fragments after injection re-labelling; what the tool interlock joins over.
    fragments: list[Fragment] = field(default_factory=list)
    #: 'caller' | 'index' | 'none'. The console shows this because "the model had no
    #: context" and "the model ignored its context" are different incidents.
    retrieved_by: str = "none"
    retrieval_ms: float = 0.0
    elapsed_ms: float = 0.0

    @property
    def degraded(self) -> bool:
        return bool(self.dropped)

    @property
    def buffered(self) -> bool:
        return self.mode == "buffered"


@dataclass
class LaneA:
    """Runs the pre-flight detectors concurrently under a hard deadline."""

    policy: Policy
    detectors: list[Detector] = field(default_factory=list)
    stakes_model: StakesModel | None = None
    #: Injected so the difficulty model can be swapped for a trained RouteLLM 'mf'
    #: controller without touching Lane A.
    router: Router | None = None
    deadline_ms: float = 120.0
    #: Interlock's own index over the corpus. Used **only** when the caller attached no
    #: context of its own: Interlock is a proxy, not a RAG stack, and silently replacing
    #: a customer's retrieval with ours would make every downstream number ours too.
    retriever: RetrievalPort | None = None
    #: Retrieval's slice of the pre-flight budget. Measured at ~3 ms on the 45-document
    #: corpus, so this is a ceiling for a pathological case, not an expectation.
    retrieval_deadline_ms: float = 40.0

    def __post_init__(self) -> None:
        if self.stakes_model is None:
            self.stakes_model = StakesModel(policy=self.policy)
        if self.router is None:
            self.router = Router(policy=self.policy)

    async def run(self, ctx: PreflightContext) -> PreflightResult:
        deadline = Deadline(budget_ms=self.deadline_ms)

        # First, because stakes prices from what was retrieved, and the injection
        # detector scans each retrieved chunk on its own.
        retrieved_by, retrieval_ms = await self._retrieve(ctx)

        # Inline: deterministic, sub-millisecond, and everything downstream needs it.
        assert self.stakes_model is not None
        stakes = self.stakes_model.estimate(ctx)

        signals, hard_rules, findings, dropped = await self._race(ctx, deadline)

        # An injected fragment is re-labelled before it can influence a tool call.
        fragments = list(ctx.retrieved)
        for detector in self.detectors:
            relabel = getattr(detector, "untrusted_fragments", None)
            if callable(relabel):
                fragments = relabel(fragments)

        # A pre-flight flag forces the buffered path regardless of stakes: something
        # already looks wrong, so the ladder must stay wide open (ADR-003).
        flagged = bool(hard_rules) or any(
            signal.name.startswith("injection") and signal.raw >= 0.6 for signal in signals
        )
        tier, route_reason = self._route(stakes, flagged, ctx, retrieved_by)
        mode: GateMode = (
            "buffered"
            if flagged or stakes.impact_inr >= self.policy.thresholds.buffer_above_impact_inr
            else "unbuffered"
        )
        if flagged:
            findings.append("pre-flight flag raised: buffering engaged regardless of stakes")

        return PreflightResult(
            stakes=stakes,
            stakes_id=new_stakes_id(),
            tier=tier,
            route_reason=route_reason,
            mode=mode,
            signals=signals,
            hard_rules=hard_rules,
            findings=findings,
            dropped=dropped,
            fragments=fragments,
            retrieved_by=retrieved_by,
            retrieval_ms=retrieval_ms,
            elapsed_ms=deadline.elapsed_ms,
        )

    async def _retrieve(self, ctx: PreflightContext) -> tuple[str, float]:
        """Fill ``ctx.retrieved`` from the index, if and only if the caller sent none."""
        if ctx.retrieved:
            return "caller", 0.0
        if self.retriever is None:
            return "none", 0.0
        question = ctx.last_user_message.strip()
        if not question:
            return "none", 0.0

        sub = Deadline(budget_ms=self.retrieval_deadline_ms)
        try:
            result = await asyncio.wait_for(
                self.retriever.retrieve(question),
                timeout=self.retrieval_deadline_ms / 1000.0,
            )
        except Exception:
            # Fail open, and broadly: a timeout, a corrupt index, a missing sqlite-vec
            # extension. See the module docstring -- less grounding, not a failed
            # request. The absence is reported as ``retrieved_by='none'`` rather than
            # swallowed, so the console can tell "no context" from "ignored context".
            return "none", sub.elapsed_ms
        ctx.retrieved = list(result.fragments)
        return ("index" if ctx.retrieved else "none"), sub.elapsed_ms

    # ------------------------------------------------------------------ #

    async def _race(
        self, ctx: PreflightContext, deadline: Deadline
    ) -> tuple[list[SignalReading], list[HardRule], list[str], list[str]]:
        """Run every detector concurrently; cancel whatever has not finished."""
        signals: list[SignalReading] = []
        hard_rules: list[HardRule] = []
        findings: list[str] = []
        dropped: list[str] = []

        if not self.detectors:
            return signals, hard_rules, findings, dropped

        tasks: dict[asyncio.Task[DetectorOutcome], Detector] = {
            asyncio.create_task(detector.scan(ctx)): detector for detector in self.detectors
        }
        done, pending = await asyncio.wait(
            tasks.keys(),
            timeout=deadline.remaining_seconds(),
            return_when=asyncio.ALL_COMPLETED,
        )

        # Dropped, not awaited. Cancel and move on; the request does not wait.
        for task in pending:
            task.cancel()
            detector = tasks[task]
            dropped.append(detector.name)
            signals.append(SignalReading(name=detector.name, raw=0.0, prob=None))
            findings.append(
                f"{detector.name}: dropped, exceeded the {self.deadline_ms:.0f} ms budget"
            )

        for task in done:
            detector = tasks[task]
            try:
                outcome = task.result()
            except Exception as exc:
                dropped.append(detector.name)
                signals.append(SignalReading(name=detector.name, raw=0.0, prob=None))
                findings.append(f"{detector.name}: failed ({exc!r})")
                continue
            signals.extend(outcome.signals)
            hard_rules.extend(outcome.hard_rules)
            findings.extend(outcome.findings)

        return signals, hard_rules, findings, dropped

    def _route(
        self, stakes: Stakes, flagged: bool, ctx: PreflightContext, retrieved_by: str
    ) -> tuple[str, str]:
        """Pick the model tier from the *same* estimate the risk engine will price with.

        Contribution 1 in its most literal form. The reason string is recorded on the
        request so a single trace proves the router and the guardrail agreed on stakes
        rather than being two separately tuned systems.
        """
        if flagged:
            # A pre-flight flag outranks the router entirely. Something already looks
            # wrong, and asking a difficulty model whether the cheap tier could cope
            # would be answering the wrong question.
            return "strong", "preflight_flag"
        assert self.router is not None
        decision = self.router.route(
            stakes=stakes,
            question=ctx.last_user_message,
            retrieved=list(ctx.retrieved),
            # 'none' means retrieval never ran. The router must not read that as a hard
            # question -- see HeuristicDifficulty.score.
            retrieval_attempted=retrieved_by != "none",
        )
        return decision.tier, decision.reason
