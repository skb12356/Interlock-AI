"""Where Interlock's added latency actually goes.

The headline claim is *"≤ 120 ms added p95, time-to-first-token statistically
unchanged"*. A single ``overhead_ms`` number can support that claim or fail it, but it
can never explain it — and when the number is bad, "the guardrail is slow" is not an
actionable statement. So overhead is attributed to the lane that spent it.

Three properties this is careful to get right, because each is a way to report a
latency figure that is precise and misleading:

**Only work on the critical path counts.** Lane B runs *concurrently with generation*.
Time it spends is not time the customer waits — unless the commit gate is holding a
sentence for it, which is the one case where it becomes user-visible. Counting all of
Lane B's wall-clock as overhead would make a correctly-designed system look slow.

**Time-to-first-token is measured separately from total overhead.** They answer
different questions. TTFT is what a customer perceives as responsiveness; total overhead
is what the SLA is written against. A system can be excellent at one and poor at the
other, and a single number hides which.

**The p95 comes from the measured distribution.** Not from a mean plus a fudge factor,
and not from a target. `TODO.md` says the Day-5 p95 must be measured, and a percentile
over fewer than 20 samples is reported as the max it actually is.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

__all__ = ["LANES", "LatencyRecorder", "LatencySample", "percentile"]

#: The lanes overhead is attributed to. ``gate_hold`` is deliberately separate from
#: ``lane_b``: Lane B's concurrent work is free, and only the part where the gate was
#: actually waiting on it is latency the customer experienced.
LANES: tuple[str, ...] = (
    "lane_a",      # pre-flight: retrieval, detectors, stakes, routing
    "gate_hold",   # the commit gate waiting on a verdict before releasing a sentence
    "repair",      # L2: re-prompting the model
    "reroute",     # L3: regenerating on a stronger tier
    "interlock",   # the tool-call interlock, including the durable hold write
    "ledger",      # anything the request path had to await before responding
)

#: Below this, a percentile is a description of the sample rather than an estimate of
#: the distribution, and is reported as the max it literally is.
MIN_SAMPLES_FOR_PERCENTILE = 20

#: Rolling window per lane. Bounded because this lives in a long-running process and an
#: unbounded list of every request's timing is a memory leak with a graph attached.
WINDOW = 2_000


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. No interpolation, so the result is a value that
    actually occurred rather than one between two that did."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class LatencySample:
    """One request's overhead, attributed."""

    request_id: str
    #: Interlock's total added latency: the sum of the lanes below, measured.
    overhead_ms: float
    #: Time to the first token the customer saw. Includes the upstream's own latency,
    #: so it is only comparable against a baseline measured the same way.
    ttft_ms: float
    by_lane: dict[str, float] = field(default_factory=dict)
    buffered: bool = False
    tier: str = ""

    @property
    def attributed_ms(self) -> float:
        return sum(self.by_lane.values())

    @property
    def unattributed_ms(self) -> float:
        """Overhead no lane claimed.

        Reported rather than distributed. A large unattributed remainder means something
        is spending time nobody instrumented, and silently folding it into the nearest
        lane would hide exactly the thing worth finding.
        """
        return max(0.0, self.overhead_ms - self.attributed_ms)


@dataclass
class LatencyRecorder:
    """Rolling per-lane latency, and the percentiles the claim is made from."""

    window: int = WINDOW
    samples: deque[LatencySample] = field(default_factory=deque, init=False)
    _by_lane: dict[str, deque[float]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # The maxlen has to come from the instance, not from the module constant. A
        # default_factory closing over WINDOW makes `window` configurable in name only,
        # which is worse than not offering the parameter: it silently ignores what the
        # caller asked for.
        self.samples = deque(maxlen=self.window)

    def record(self, sample: LatencySample) -> None:
        self.samples.append(sample)
        for lane, value in sample.by_lane.items():
            bucket = self._by_lane.setdefault(lane, deque(maxlen=self.window))
            bucket.append(value)

    # ------------------------------------------------------------------ #

    def overhead_p95(self) -> float:
        return percentile([s.overhead_ms for s in self.samples], 0.95)

    def ttft_p95(self) -> float:
        return percentile([s.ttft_ms for s in self.samples if s.ttft_ms > 0], 0.95)

    def lane_p95(self, lane: str) -> float:
        return percentile(list(self._by_lane.get(lane, ())), 0.95)

    def report(self, *, budget_ms: float = 120.0) -> dict[str, Any]:
        """Everything the Day-5 latency claim is made from, with its caveats."""
        overheads = [s.overhead_ms for s in self.samples]
        n = len(overheads)
        notes: list[str] = []

        if n < MIN_SAMPLES_FOR_PERCENTILE:
            notes.append(
                f"only {n} samples -- below {MIN_SAMPLES_FOR_PERCENTILE}, so the figure "
                f"below is the maximum observed and must not be reported as a p95"
            )

        # Buffered and unbuffered traffic have genuinely different latency profiles, and
        # pooling them produces a p95 that describes neither. Buffered traffic is the
        # minority by design (stakes-gated), so a pooled p95 would mostly reflect the
        # unbuffered path while the SLA risk lives in the other one.
        buffered = [s.overhead_ms for s in self.samples if s.buffered]
        unbuffered = [s.overhead_ms for s in self.samples if not s.buffered]

        unattributed = [s.unattributed_ms for s in self.samples]
        mean_unattributed = sum(unattributed) / n if n else 0.0
        if n and mean_unattributed > 0.15 * (sum(overheads) / n):
            notes.append(
                f"{mean_unattributed:.1f} ms of mean overhead is unattributed to any lane "
                f"-- something is spending time nobody instrumented"
            )

        p95 = percentile(overheads, 0.95)
        if n >= MIN_SAMPLES_FOR_PERCENTILE and p95 > budget_ms:
            notes.append(f"p95 overhead {p95:.0f} ms exceeds the {budget_ms:.0f} ms budget")

        return {
            "n": n,
            "budget_ms": budget_ms,
            "overhead_p50_ms": round(percentile(overheads, 0.50), 2),
            "overhead_p95_ms": round(p95, 2),
            "overhead_max_ms": round(max(overheads), 2) if overheads else 0.0,
            "within_budget": p95 <= budget_ms if n >= MIN_SAMPLES_FOR_PERCENTILE else None,
            "ttft_p50_ms": round(percentile([s.ttft_ms for s in self.samples if s.ttft_ms > 0], 0.5), 2),
            "ttft_p95_ms": round(self.ttft_p95(), 2),
            "buffered": {
                "n": len(buffered),
                "p95_ms": round(percentile(buffered, 0.95), 2),
            },
            "unbuffered": {
                "n": len(unbuffered),
                "p95_ms": round(percentile(unbuffered, 0.95), 2),
            },
            "by_lane_p95_ms": {
                lane: round(self.lane_p95(lane), 2)
                for lane in LANES
                if self._by_lane.get(lane)
            },
            "unattributed_mean_ms": round(mean_unattributed, 2),
            "notes": notes,
            "caveat": (
                "Lane B runs concurrently with generation; only the portion the commit "
                "gate actually waited on ('gate_hold') is counted, because the rest is "
                "not time the customer spent waiting."
            ),
        }


@dataclass
class LaneTimer:
    """Accumulates per-lane timings for one request.

    A plain dict with an add method, deliberately: a context-manager-per-lane API reads
    better and would tempt the request path into ``async with`` blocks around work that
    is sometimes cancelled, where the exit handler's behaviour is the last thing anyone
    wants to reason about at 3am.
    """

    lanes: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def add(self, lane: str, ms: float) -> None:
        if ms > 0:
            self.lanes[lane] += ms

    def snapshot(self) -> dict[str, float]:
        return {lane: round(value, 3) for lane, value in self.lanes.items() if value > 0}
