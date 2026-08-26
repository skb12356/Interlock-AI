"""The router: how much compute is this request worth?

Contribution 1, on the spend side. The guardrail decides how hard to check; this decides
how much to spend — and **both read the same stakes estimate**. That sharing is the whole
thesis, so it is made visible rather than merely true: every routing decision carries the
``stakes_id`` the risk engine will also carry, and one trace can be used to prove the two
budgets came from one number.

Two signals, combined:

**Stakes** decides the floor. A ₹40,000 loan question goes to the strong tier because
being wrong is expensive, regardless of how easy the question looks. This is a policy
threshold, not a learned one, and it is deliberately the dominant term — a router that
could talk itself out of the strong model on a high-stakes question has broken the
guarantee the stakes estimate exists to provide.

**Difficulty** decides within that. Among requests the stakes did not force, a cheap
model handles most of them fine. RouteLLM's finding is that a small classifier over the
query recovers most of the strong model's quality at a fraction of the cost; the ``mf``
controller is a matrix-factorisation model over (query, model) pairs. That needs training
data this build does not have, so what ships is a **deterministic difficulty score** with
the same interface — retrieval agreement, question complexity, whether the corpus even
answers it — and the learned controller drops in behind ``DifficultyModel`` later.

The honest note: a deterministic difficulty score is weaker than a trained controller and
is labelled as such in the trace (``route_reason``). It is not labelled ``router_mf``,
because that would claim a mechanism this build does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from interlock.core.policy import Policy
from interlock.core.types import Fragment, Stakes

__all__ = ["DifficultyModel", "HeuristicDifficulty", "RouteDecision", "Router"]

#: Difficulty at or above which a request goes to the strong tier even though the stakes
#: did not force it. Above this the cheap model's answer is likely to need a repair, and
#: a repair costs ~14 s and a second generation -- so upgrading up front is cheaper than
#: discovering the problem at the commit gate.
DIFFICULTY_THRESHOLD = 0.65

_MULTI_PART = re.compile(r"\b(and also|as well as|additionally|furthermore|;)\b", re.IGNORECASE)
_COMPARATIVE = re.compile(
    r"\b(compare|versus|vs\.?|difference between|better|cheaper|which of)\b", re.IGNORECASE
)
_CONDITIONAL = re.compile(r"\b(if|unless|provided that|in case|when|whether)\b", re.IGNORECASE)
_NUMERIC = re.compile(r"\b(calculate|how much|how many|total|interest|emi|rate)\b", re.IGNORECASE)


class DifficultyModel(Protocol):
    """The seam a RouteLLM ``mf`` controller drops into.

    Returns a score in [0, 1]: how likely the cheap model is to produce an answer that
    needs intervening on.
    """

    name: str

    def score(
        self, question: str, retrieved: list[Fragment], *, retrieval_attempted: bool = True
    ) -> float: ...


@dataclass
class HeuristicDifficulty:
    """Deterministic stand-in for the learned controller.

    Every term is something that genuinely makes a question harder for a small model,
    and each is cheap enough to compute inside Lane A's budget.
    """

    name: str = "heuristic-v1"

    def score(
        self, question: str, retrieved: list[Fragment], *, retrieval_attempted: bool = True
    ) -> float:
        signals: list[float] = []

        # "Nothing retrieved" and "retrieval never ran" look identical here and mean
        # opposite things.
        #
        # Retrieval RAN and found nothing: the model must answer from parameters, which
        # is where a small model is weakest and most confident. Genuinely hard.
        #
        # Retrieval never ran -- no index built, or the caller does its own RAG and
        # attached nothing -- and that is evidence about our configuration, not about
        # the question. Scoring it 1.0 would route EVERY request to the strong tier in
        # exactly the deployment shape the proxy is designed for, silently destroying
        # the routing saving. Caught by a contract test that asked why a branch-hours
        # question had stopped being cheap.
        if not retrieved and retrieval_attempted:
            signals.append(1.0)
        # NOTHING is derived from the retrieved set beyond "was it empty", and that is
        # a deliberate omission recorded after two failed attempts:
        #
        #   1. Document COUNT. Retrieval always returns k passages, so counting them
        #      measures the retriever's k and nothing about the question. Scored 1.0 on
        #      every retrieved request and sent all traffic to the strong tier.
        #   2. Score SPREAD. Sound in principle -- a decisive top hit means retrieval
        #      knows where the answer is. But the scores here come from Reciprocal Rank
        #      Fusion, which is a sum of 1/(60 + rank): 0.02433 against 0.02334 across
        #      four hits. RRF carries RANK information and deliberately discards
        #      magnitude, so any measure built on the magnitudes is reading the fusion
        #      constant. Scored 0.90 on a branch-hours question.
        #
        # Both were caught by the same contract test asking why an easy question had
        # stopped being cheap. A retrieval-quality term needs a score with a meaningful
        # scale -- a real embedding cosine -- which arrives with D-009's bge-small swap.
        # Until then this model reasons about the QUESTION, and says so.

        words = question.split()
        # Long questions carry more clauses to satisfy at once.
        signals.append(min(1.0, len(words) / 60.0))

        for pattern, weight in (
            (_MULTI_PART, 0.8),      # two questions in one
            (_COMPARATIVE, 0.9),     # requires holding two things side by side
            (_CONDITIONAL, 0.5),     # branching on a condition
            (_NUMERIC, 0.7),         # arithmetic, where small models fail quietly
        ):
            if pattern.search(question):
                signals.append(weight)

        # Max rather than mean: difficulty is a bottleneck, not an average. One
        # genuinely hard aspect makes the whole question hard, and averaging it against
        # three easy ones would route it cheap.
        return max(signals) if signals else 0.0


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Which tier, and why -- in words a trace can carry."""

    tier: str
    reason: str
    difficulty: float
    #: The estimate that drove this. The risk engine carries the same id.
    stakes_id: str = ""
    #: True when the stakes threshold forced it, regardless of difficulty.
    forced_by_stakes: bool = False

    def as_event(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "route_reason": self.reason,
            "difficulty": round(self.difficulty, 4),
            "stakes_id": self.stakes_id,
            "forced_by_stakes": self.forced_by_stakes,
        }


@dataclass
class Router:
    """Stakes first, difficulty second."""

    policy: Policy
    difficulty_model: DifficultyModel = field(default_factory=HeuristicDifficulty)
    difficulty_threshold: float = DIFFICULTY_THRESHOLD

    def route(
        self,
        *,
        stakes: Stakes,
        question: str,
        retrieved: list[Fragment],
        stakes_id: str = "",
        retrieval_attempted: bool = True,
    ) -> RouteDecision:
        threshold = self.policy.thresholds.strong_model_above_impact_inr

        # Stakes dominates, and cannot be overridden downward. A router able to talk
        # itself out of the strong model on a high-stakes question would break the
        # guarantee the stakes estimate exists to provide.
        if stakes.impact_inr >= threshold:
            return RouteDecision(
                tier="strong",
                reason="stakes_high",
                difficulty=self.difficulty_model.score(
                    question, retrieved, retrieval_attempted=retrieval_attempted
                ),
                stakes_id=stakes_id,
                forced_by_stakes=True,
            )

        difficulty = self.difficulty_model.score(
            question, retrieved, retrieval_attempted=retrieval_attempted
        )
        if difficulty >= self.difficulty_threshold:
            return RouteDecision(
                tier="strong",
                # NOT 'router_mf'. Naming it that would claim a trained matrix
                # factorisation controller this build does not have.
                reason=f"difficulty_{self.difficulty_model.name}",
                difficulty=difficulty,
                stakes_id=stakes_id,
            )

        return RouteDecision(
            tier="cheap",
            reason="stakes_low",
            difficulty=difficulty,
            stakes_id=stakes_id,
        )
