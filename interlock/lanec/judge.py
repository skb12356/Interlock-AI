"""The deep-judge anchor: ~1% of traffic, offline, as a calibration reference only.

CLAUDE.md §3 is unambiguous: **never put a generative LLM judge on the hot path.** It is
slow, it is expensive, and the literature is consistent that it is overconfident and
self-preferring. A product built on judge-everything-with-a-frontier-model is listed in
§7 as an explicit non-goal.

So why have one at all? Because the fast lanes need something to be *checked against*.
The probes and the grounding signals are cheap approximations, and an approximation with
nothing to compare it to drifts without anyone noticing. A small, offline, expensive
opinion is exactly the right instrument for that job — and exactly the wrong one for
deciding what a customer sees.

The rules this file enforces, rather than merely documents:

* **Sampled, not universal.** A hard cap on the sample rate, checked at construction. A
  judge running on 40% of traffic is not an anchor, it is the product's cost structure.
* **Offline, always.** ``judge()`` takes a completed decision, never a live one. There is
  no path from here back into a response.
* **Its verdict never overrides a decision.** It produces *agreement*, which feeds the
  meta-monitor. If the judge and the fast lane disagree, that is evidence about the fast
  lane's calibration, not an instruction to change the answer that already shipped.
* **The judge is fallible and is recorded as such.** Every verdict carries the judge's
  own confidence and the model that produced it, because "the judge said so" is not a
  ground truth and a system that treats it as one has simply moved the trust problem.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DeepJudge", "JudgeSample", "JudgeVerdict", "agreement_summary"]

#: Hard ceiling on the sample rate. The plan says ~1%; above this the "offline anchor"
#: framing stops being true and the economics change shape. Enforced, not suggested.
MAX_SAMPLE_RATE = 0.05

DEFAULT_SAMPLE_RATE = 0.01

_SYSTEM = (
    "You are auditing a bank support answer AFTER it was delivered. Decide only whether "
    "each claim in the answer is supported by the evidence provided. Do not rewrite the "
    "answer, do not judge its tone, and do not speculate about what the customer wanted. "
    "Reply with a verdict of SUPPORTED, UNSUPPORTED or UNCLEAR, then one sentence of "
    "reasoning. If the evidence is insufficient to tell, say UNCLEAR rather than guessing."
)


def build_judge_messages(
    *, question: str, answer: str, evidence: Sequence[str]
) -> list[dict[str, str]]:
    """The judge prompt.

    The evidence is presented as the *only* admissible source. A judge allowed to fall
    back on its own knowledge is answering a different question -- "is this true?" rather
    than "is this grounded?" -- and grounding is what the fast lane is being checked on.
    """
    block = "\n".join(f"- {item.strip()}" for item in evidence if item.strip()) or "- (none)"
    user = (
        f"Customer question:\n{question.strip()}\n\n"
        f"Answer that was delivered:\n{answer.strip()}\n\n"
        f"The ONLY evidence that was retrieved:\n{block}\n\n"
        "Verdict:"
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """One judge opinion. Not a ground truth -- an expensive second opinion."""

    #: 'supported' | 'unsupported' | 'unclear'
    verdict: str
    #: The judge's own stated confidence, when it gave one.
    confidence: float | None = None
    reasoning: str = ""
    model: str = ""

    @property
    def says_defective(self) -> bool | None:
        """None for 'unclear' -- which is a real answer and must not collapse to False.

        Treating "I cannot tell" as "it is fine" would make the anchor systematically
        agree with a fast lane that passed, which is precisely the bias an anchor exists
        to detect.
        """
        if self.verdict == "unclear":
            return None
        return self.verdict == "unsupported"


@dataclass(frozen=True, slots=True)
class JudgeSample:
    """A completed request the judge looked at, and whether it agreed."""

    request_id: str
    question: str
    answer: str
    #: What the fast lane concluded: True if it flagged a defect.
    fast_lane_flagged: bool
    fast_lane_probability: float
    verdict: JudgeVerdict

    @property
    def agreed(self) -> bool | None:
        """None when the judge could not tell. Excluded from agreement rates."""
        judged = self.verdict.says_defective
        if judged is None:
            return None
        return judged == self.fast_lane_flagged

    @property
    def disagreement_indicator(self) -> float | None:
        """For the e-value monitor: 1.0 on disagreement, None when unjudgeable."""
        agreed = self.agreed
        return None if agreed is None else (0.0 if agreed else 1.0)


@dataclass
class DeepJudge:
    """Decides what to sample, and records what the judge said.

    The actual model call is injected. This module owns the sampling discipline and the
    bookkeeping; it deliberately does not own a provider, so nothing here can
    accidentally acquire a synchronous call on a request path.
    """

    sample_rate: float = DEFAULT_SAMPLE_RATE
    seed: int = 20260826
    samples: list[JudgeSample] = field(default_factory=list)
    requests_seen: int = 0
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        if not 0.0 <= self.sample_rate <= MAX_SAMPLE_RATE:
            raise ValueError(
                f"sample_rate {self.sample_rate} exceeds the {MAX_SAMPLE_RATE} ceiling -- "
                f"above that this stops being an offline anchor and becomes the product's "
                f"cost structure (CLAUDE.md s3)"
            )
        self._rng = random.Random(self.seed)

    def should_judge(self) -> bool:
        self.requests_seen += 1
        return self._rng.random() < self.sample_rate

    def record(self, sample: JudgeSample) -> None:
        self.samples.append(sample)

    @staticmethod
    def parse(text: str, *, model: str = "") -> JudgeVerdict:
        """Read a verdict out of the judge's reply, tolerating the usual noise.

        An unparseable reply becomes ``unclear`` rather than a default verdict. A judge
        whose output we could not read has told us nothing, and picking a side on its
        behalf would inject our own bias into the very instrument meant to detect ours.
        """
        lowered = text.lower()
        verdict = "unclear"
        # Checked in this order: "unsupported" contains "supported" as a substring, so
        # testing for the affirmative first would misread every negative verdict.
        if "unsupported" in lowered or "not supported" in lowered:
            verdict = "unsupported"
        elif "supported" in lowered:
            verdict = "supported"

        confidence: float | None = None
        for raw in lowered.replace("%", " ").split():
            # Trailing punctuation is the norm in prose ("confidence 0.85.") and
            # float() rejects it, so a stated confidence was being silently dropped.
            token = raw.strip(".,;:()[]")
            try:
                value = float(token)
            except ValueError:
                continue
            if 0.0 <= value <= 1.0:
                confidence = value
                break

        reasoning = next(
            (line.strip() for line in text.splitlines() if len(line.strip()) > 20), ""
        )
        return JudgeVerdict(
            verdict=verdict, confidence=confidence, reasoning=reasoning[:400], model=model
        )


def agreement_summary(samples: Sequence[JudgeSample]) -> dict[str, Any]:
    """How often the fast lane and the judge reached the same conclusion.

    Reported with the *unjudgeable* count beside it. An agreement rate computed after
    silently dropping every 'unclear' looks better than the evidence supports, and the
    cases a judge cannot call are often exactly the hard ones.
    """
    judged = [s for s in samples if s.agreed is not None]
    unclear = len(samples) - len(judged)
    agreed = sum(1 for s in judged if s.agreed)

    # Split by direction, because the two disagreements mean opposite things: the fast
    # lane missing something the judge caught is an escape, and the fast lane flagging
    # something the judge thought fine is a false alarm.
    fast_missed = sum(
        1 for s in judged if not s.fast_lane_flagged and s.verdict.says_defective
    )
    fast_over = sum(
        1 for s in judged if s.fast_lane_flagged and not s.verdict.says_defective
    )

    notes: list[str] = []
    if samples and unclear / len(samples) > 0.25:
        notes.append(
            f"{unclear} of {len(samples)} samples were UNCLEAR to the judge. A judge that "
            f"cannot call a quarter of the anchor set is a weak anchor, and the agreement "
            f"rate below is computed on the remainder."
        )
    if judged and fast_missed > fast_over * 2:
        notes.append(
            "disagreements are dominated by the fast lane MISSING what the judge caught "
            "-- that is an escape pattern, not an over-flagging one"
        )

    return {
        "n_samples": len(samples),
        "n_judged": len(judged),
        "n_unclear": unclear,
        "agreement_rate": round(agreed / len(judged), 4) if judged else None,
        "fast_lane_missed": fast_missed,
        "fast_lane_over_flagged": fast_over,
        "notes": notes,
    }
