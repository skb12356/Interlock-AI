"""The six target metrics, defined once so nothing can quietly redefine them.

Every one of these is a number the project will be judged on, which makes the exact
definition load-bearing. Two of them have a tempting wrong version:

**Pre-Action Catch Rate** is not "how many defects did we detect". It is how many were
stopped *before a human read them or a tool executed*. A defect that was correctly
flagged after the answer shipped is a detection and an escape at the same time, and
counting it as a catch is the single easiest way to make this number look good.

**False interventions** is not "how often were we wrong". It is how often we intervened
on traffic that did not deserve it — measured only over the clean cases, because a
defective case we intervened on was not a false alarm however heavy the action.

Everything reports a Wilson interval, not a bare point estimate (CLAUDE.md §9). On a
200-case set a rate of 0.90 has an interval roughly ±0.05 wide, and reporting the point
alone invites a comparison between two numbers that are not distinguishable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = ["MetricResult", "MetricSet", "wilson_interval"]

#: Actions that stop the defect reaching a person or a tool. L1 annotates and ships, so
#: it is deliberately NOT here: the reader still receives the claim, with a note beside
#: it. Counting an annotation as a catch would let the system claim credit for
#: delivering the defect politely.
PRE_ACTION_ACTIONS = frozenset({"L2_repair", "L3_reroute", "L4_hold", "L5_block"})


def wilson_interval(successes: int, trials: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Correct near 0 and 1, where normal approximation is not.

    A catch rate of 60/60 has a normal-approximation interval of exactly zero width,
    which would let the report claim certainty from sixty observations.
    """
    if trials == 0:
        return (0.0, 0.0)
    proportion = successes / trials
    denominator = 1 + z**2 / trials
    centre = (proportion + z**2 / (2 * trials)) / denominator
    spread = (
        z
        * math.sqrt(proportion * (1 - proportion) / trials + z**2 / (4 * trials**2))
        / denominator
    )
    return (max(0.0, centre - spread), min(1.0, centre + spread))


@dataclass(frozen=True, slots=True)
class MetricResult:
    """One metric, its interval, and whether it met its target."""

    name: str
    value: float
    unit: str
    target: str
    met: bool | None
    #: Populated for rates; None for costs and latencies.
    ci: tuple[float, float] | None = None
    numerator: int | None = None
    denominator: int | None = None
    note: str = ""

    def render(self) -> str:
        if self.unit == "%":
            body = f"{self.value * 100:6.2f}%"
            if self.ci:
                body += f"  [{self.ci[0] * 100:.1f}, {self.ci[1] * 100:.1f}]"
            if self.denominator:
                body += f"  n={self.denominator}"
        elif self.unit == "ms":
            body = f"{self.value:8.2f} ms"
        else:
            body = f"{self.value:10.4f} {self.unit}"
        flag = "" if self.met is None else ("  PASS" if self.met else "  MISS")
        # Sub-rows carry their note inline; top-level metrics keep theirs for the JSON,
        # where there is room for a full sentence.
        inline = f"   {self.note}" if self.note and self.name.startswith("  ...") else ""
        return f"  {self.name:28} {body}{flag}{inline}"


@dataclass
class MetricSet:
    """All six, plus whatever else the run measured."""

    metrics: list[MetricResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, metric: MetricResult) -> None:
        self.metrics.append(metric)

    def by_name(self, name: str) -> MetricResult | None:
        return next((m for m in self.metrics if m.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": [
                {
                    "name": m.name,
                    "value": m.value,
                    "unit": m.unit,
                    "target": m.target,
                    "met": m.met,
                    "ci": list(m.ci) if m.ci else None,
                    "numerator": m.numerator,
                    "denominator": m.denominator,
                    "note": m.note,
                }
                for m in self.metrics
            ],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        lines = [metric.render() for metric in self.metrics]
        if self.notes:
            lines.append("")
            lines.extend(f"  ! {note}" for note in self.notes)
        return "\n".join(lines)
