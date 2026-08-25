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

from interlock.core.clock import Deadline
from interlock.core.ids import new_stakes_id
from interlock.core.policy import Policy
from interlock.core.types import Fragment, GateMode, SignalReading, Stakes
from interlock.risk.objective import HardRule
from interlock.signals.base import Detector, DetectorOutcome, PreflightContext
from interlock.signals.stakes import StakesModel

__all__ = ["LaneA", "PreflightResult"]


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
    deadline_ms: float = 120.0

    def __post_init__(self) -> None:
        if self.stakes_model is None:
            self.stakes_model = StakesModel(policy=self.policy)

    async def run(self, ctx: PreflightContext) -> PreflightResult:
        deadline = Deadline(budget_ms=self.deadline_ms)

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
        tier, route_reason = self._route(stakes, flagged)
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
            elapsed_ms=deadline.elapsed_ms,
        )

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

    def _route(self, stakes: Stakes, flagged: bool) -> tuple[str, str]:
        """Pick the model tier from the *same* estimate the risk engine will price with.

        Contribution 1 in its most literal form. The reason string is recorded on the
        request so a single trace proves the router and the guardrail agreed on stakes
        rather than being two separately tuned systems.
        """
        if flagged:
            return "strong", "preflight_flag"
        if stakes.impact_inr >= self.policy.thresholds.strong_model_above_impact_inr:
            return "strong", "stakes_high"
        return "cheap", "stakes_low"
