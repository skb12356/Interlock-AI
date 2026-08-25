"""Lane A — the only synchronous work on the critical path.

The behaviour these tests exist to protect is the deadline discipline: a slow detector
is *cancelled*, and its absence is *recorded*. A lane that quietly waits for a slow
detector has no deadline at all, and the latency claim built on top of it is fiction.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from interlock.core.policy import Policy, load_policy
from interlock.core.types import Fragment, SignalReading
from interlock.gateway.lane_a import LaneA
from interlock.risk.objective import HardRule
from interlock.signals.base import DetectorOutcome, PreflightContext
from interlock.signals.canary import CanaryDetector, CanaryRegistry
from interlock.signals.injection import InjectionDetector, PatternInjectionBackend
from interlock.signals.pii import PIIDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "corpus"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(REPO_ROOT / "policies" / "banking.yaml")


def _ctx(**kwargs: object) -> PreflightContext:
    base: dict[str, object] = {"request_id": "req_1", "tenant_id": "demo"}
    base.update(kwargs)
    return PreflightContext(**base)  # type: ignore[arg-type]


class SlowDetector:
    """Deliberately overruns the budget."""

    name = "slowpoke"

    def __init__(self, delay_s: float = 5.0) -> None:
        self.delay_s = delay_s
        self.completed = False

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        await asyncio.sleep(self.delay_s)
        self.completed = True
        return DetectorOutcome(signals=[SignalReading(name="slowpoke", raw=1.0)])


class ExplodingDetector:
    name = "exploder"

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        raise RuntimeError("detector is broken")


class FastDetector:
    name = "fast"

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        return DetectorOutcome(signals=[SignalReading(name="fast", raw=0.25, prob=0.25)])


# --------------------------------------------------------------------------- #
# Deadline discipline
# --------------------------------------------------------------------------- #


async def test_a_slow_detector_is_dropped_not_awaited(policy: Policy) -> None:
    """The whole point of the lane. If this regresses, the latency budget is a fiction."""
    slow = SlowDetector(delay_s=5.0)
    lane = LaneA(policy=policy, detectors=[slow], deadline_ms=50)

    result = await asyncio.wait_for(lane.run(_ctx()), timeout=2.0)

    assert "slowpoke" in result.dropped
    assert result.elapsed_ms < 1000  # nowhere near the detector's 5 s
    assert slow.completed is False  # it was cancelled, not left running to completion


async def test_a_dropped_detector_is_recorded_with_prob_none(policy: Policy) -> None:
    """A missing signal is information: 'we did not check' differs from 'we found
    nothing', and the console must be able to tell a reviewer which happened."""
    lane = LaneA(policy=policy, detectors=[SlowDetector()], deadline_ms=30)
    result = await lane.run(_ctx())

    dropped_signal = next(s for s in result.signals if s.name == "slowpoke")
    assert dropped_signal.prob is None
    assert any("dropped" in f for f in result.findings)


async def test_dropping_is_never_silent(policy: Policy) -> None:
    lane = LaneA(policy=policy, detectors=[SlowDetector()], deadline_ms=30)
    result = await lane.run(_ctx())
    assert result.degraded is True


async def test_a_fast_detector_still_reports_when_another_is_dropped(
    policy: Policy,
) -> None:
    """One slow detector must not cost us the signals that did arrive."""
    lane = LaneA(policy=policy, detectors=[FastDetector(), SlowDetector()], deadline_ms=50)
    result = await lane.run(_ctx())

    assert result.dropped == ["slowpoke"]
    assert any(s.name == "fast" and s.prob == 0.25 for s in result.signals)


async def test_a_broken_detector_does_not_fail_the_request(policy: Policy) -> None:
    lane = LaneA(policy=policy, detectors=[ExplodingDetector(), FastDetector()], deadline_ms=200)
    result = await lane.run(_ctx())

    assert "exploder" in result.dropped
    assert any("failed" in f for f in result.findings)
    assert any(s.name == "fast" for s in result.signals)


async def test_the_lane_completes_within_its_budget(policy: Policy) -> None:
    lane = LaneA(policy=policy, detectors=[SlowDetector(), SlowDetector()], deadline_ms=60)
    result = await lane.run(_ctx())
    assert result.elapsed_ms < 600


async def test_stakes_are_produced_even_when_every_detector_is_dropped(
    policy: Policy,
) -> None:
    """Stakes runs inline precisely so this holds: without it there is no budget to
    route on or price with, so a degraded request would have none at all."""
    lane = LaneA(policy=policy, detectors=[SlowDetector()], deadline_ms=20)
    result = await lane.run(_ctx(messages=[{"role": "user", "content": "Can I prepay my loan?"}]))
    assert result.stakes.domain == "prepayment"
    assert result.stakes_id.startswith("stk_")


async def test_lane_a_runs_with_no_detectors_at_all(policy: Policy) -> None:
    result = await LaneA(policy=policy, detectors=[]).run(_ctx())
    assert result.dropped == []
    assert result.stakes is not None


# --------------------------------------------------------------------------- #
# One estimate, two budgets (Contribution 1)
# --------------------------------------------------------------------------- #


async def test_high_stakes_routes_strong_and_engages_the_buffer(policy: Policy) -> None:
    """The same number decides which model runs and whether the gate holds a sentence.
    Provable from one result object, which is what the trace records."""
    lane = LaneA(policy=policy, detectors=[])
    result = await lane.run(
        _ctx(
            messages=[{"role": "user", "content": "Is there a prepayment penalty?"}],
            retrieved=[
                Fragment(
                    text="Clause 9.1 ...", provenance="retrieved_verified", domain="prepayment"
                )
            ],
        )
    )
    assert result.tier == "strong"
    assert result.route_reason == "stakes_high"
    assert result.mode == "buffered"


async def test_low_stakes_routes_cheap_and_does_not_buffer(policy: Policy) -> None:
    """~80% of traffic. TTFT delta must be ~0 here or the p50 claim dies (ADR-003)."""
    lane = LaneA(policy=policy, detectors=[])
    result = await lane.run(
        _ctx(
            messages=[{"role": "user", "content": "What time does the branch open?"}],
            retrieved=[
                Fragment(
                    text="Fort branch ...", provenance="retrieved_verified", domain="branch_info"
                )
            ],
        )
    )
    assert result.tier == "cheap"
    assert result.route_reason == "stakes_low"
    assert result.mode == "unbuffered"


async def test_the_router_and_the_gate_read_the_same_estimate(policy: Policy) -> None:
    """If these two ever disagree, the thesis is broken -- they are not allowed to be
    separately tuned systems (invariant 1)."""
    lane = LaneA(policy=policy, detectors=[])
    for content, retrieved_domain in [
        ("prepayment penalty?", "prepayment"),
        ("branch hours?", "branch_info"),
    ]:
        result = await lane.run(
            _ctx(
                messages=[{"role": "user", "content": content}],
                retrieved=[
                    Fragment(text="...", provenance="retrieved_verified", domain=retrieved_domain)
                ],
            )
        )
        routes_strong = result.tier == "strong"
        buffers = result.mode == "buffered"
        assert routes_strong == buffers


# --------------------------------------------------------------------------- #
# A pre-flight flag overrides stakes
# --------------------------------------------------------------------------- #


async def test_an_injected_chunk_forces_buffering_on_low_stakes_traffic(
    policy: Policy,
) -> None:
    """Something already looks wrong, so the ladder must stay wide open regardless of
    how cheap the question was."""
    poisoned = (CORPUS / "d044.md").read_text(encoding="utf-8")
    lane = LaneA(
        policy=policy,
        detectors=[InjectionDetector(backend=PatternInjectionBackend())],
    )
    result = await lane.run(
        _ctx(
            messages=[{"role": "user", "content": "summarise this"}],
            retrieved=[
                Fragment(
                    text=poisoned,
                    provenance="retrieved_verified",
                    doc_id="d044",
                    domain="claims",
                )
            ],
        )
    )
    assert result.mode == "buffered"
    assert result.tier == "strong"
    assert result.route_reason == "preflight_flag"


async def test_an_injected_fragment_is_relabelled_before_it_can_reach_a_tool(
    policy: Policy,
) -> None:
    poisoned = (CORPUS / "d044.md").read_text(encoding="utf-8")
    lane = LaneA(policy=policy, detectors=[InjectionDetector(backend=PatternInjectionBackend())])
    result = await lane.run(
        _ctx(retrieved=[Fragment(text=poisoned, provenance="retrieved_verified", doc_id="d044")])
    )
    assert result.fragments[0].provenance == "retrieved_untrusted"


async def test_a_canary_leak_in_context_surfaces_as_a_hard_rule(policy: Policy) -> None:
    registry = CanaryRegistry()
    canary = registry.mint("demo")
    detector = CanaryDetector(registry=registry)

    # The egress half is what fires; confirm it produces a rule Lane A would carry.
    outcome = detector.scan_egress(f"leaked {canary}")
    assert isinstance(outcome.hard_rules[0], HardRule)
    assert outcome.hard_rules[0].action == "L5_block"


async def test_the_full_detector_set_stays_inside_the_budget(policy: Policy) -> None:
    """The realistic configuration, on realistic input. This is the number that becomes
    the measured p95 at D5-A2."""
    registry = CanaryRegistry()
    registry.mint("demo")
    lane = LaneA(
        policy=policy,
        detectors=[
            InjectionDetector(backend=PatternInjectionBackend()),
            PIIDetector(),
            CanaryDetector(registry=registry),
        ],
        deadline_ms=120,
    )
    ctx = _ctx(
        messages=[
            {"role": "system", "content": "You are a bank support assistant."},
            {"role": "user", "content": "Can I prepay Rs. 5,00,000 on my home loan?"},
        ],
        retrieved=[
            Fragment(
                text=(CORPUS / f"d{n:03d}.md").read_text(encoding="utf-8"),
                provenance="retrieved_verified",
                doc_id=f"d{n:03d}",
                domain="prepayment",
            )
            for n in (1, 2, 20)
        ],
    )
    result = await lane.run(ctx)

    assert result.dropped == []
    assert result.elapsed_ms < 120
