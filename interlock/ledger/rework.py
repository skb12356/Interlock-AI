"""Rework: what a bad answer cost *after* it was given.

A wrong answer is not free once it ships. The customer asks again, the assistant
regenerates, a human is escalated to — and every one of those is spend caused by the
first answer failing. A ledger that counts only the original request under-states the
cost of *not* checking, which is precisely the comparison Interlock exists to make.

So this builds a session graph: child requests are attributed back to the parent whose
answer caused them, and the child's cost — plus the policy's human-review cost where a
human got involved — is charged to the parent.

Three kinds of edge, in descending order of how sure we are:

* ``human_escalation`` (confidence 1.0) — a hold was raised and a reviewer acted on it.
  There is no inference here; the hold *is* the edge.
* ``regenerate`` (confidence 0.95) — the client explicitly asked for another answer.
  Near-certain, but not 1.0: a client can regenerate out of curiosity.
* ``retry`` (confidence ≤ 0.9) — the customer re-asked something similar within a short
  window. This one is **inferred**, and the confidence is stored on the edge rather than
  thrown away, because an attribution that might be wrong should be visibly might-be-wrong
  in the number it feeds.

**The confidence is stored, and it is used.** A retry at 0.72 confidence charges 72% of
the child's cost back to the parent, not all of it. Charging the full amount on a maybe
would let a coincidental follow-up question inflate the rework figure, and rework is the
number that makes Interlock's case — so it is the one that most needs to resist
flattering itself.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "RETRY_CONFIDENCE_FLOOR",
    "ReworkEdge",
    "ReworkLedger",
    "SessionTurn",
    "similarity",
]

#: Two turns must be at least this similar to count as a retry. The plan says cosine
#: >= 0.90; this uses token-set cosine, which is the same measure over a bag of words.
RETRY_SIMILARITY = 0.90

#: And close enough in time. A customer who asks the same thing tomorrow is having a
#: new conversation, not retrying.
RETRY_WINDOW_S = 120.0

#: Confidence ceilings per edge kind. A retry never reaches 1.0 however similar the
#: wording, because similarity is evidence of a retry and not proof of one.
CONFIDENCE: dict[str, float] = {
    "human_escalation": 1.0,
    "regenerate": 0.95,
    "retry": 0.90,
}

#: Confidence given to a retry sitting exactly on the similarity threshold.
#:
#: This is not a free parameter -- it has to be above zero. Scaling confidence linearly
#: from 0 at the threshold would mean a match that just clears the bar charges nothing,
#: which makes the threshold itself meaningless: everything from 0.90 to 0.91 similarity
#: would be detected and then costed at approximately zero. Half is the honest reading
#: of "this cleared the bar and no more".
RETRY_CONFIDENCE_FLOOR = 0.5

_WORD = re.compile(r"[a-z0-9]+")

#: Function words shared by every question. Left in, a bag-of-words cosine between two
#: unrelated banking questions sits around 0.5 purely on "what is the my for".
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "that",
        "the",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "will",
        "with",
        "you",
        "your",
    ]
)


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS}


def similarity(left: str, right: str) -> float:
    """Token-set cosine between two turns, in [0, 1]."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    # Clamped: identical inputs come out at 1.0000000000000002 in binary floating
    # point, and a "similarity" above 1 would look like a bug to anyone reading a
    # rework edge's reason string.
    return min(1.0, overlap / ((len(a) ** 0.5) * (len(b) ** 0.5)))


@dataclass(frozen=True, slots=True)
class SessionTurn:
    """One request in a conversation, with what it cost."""

    request_id: str
    session_id: str
    question: str
    ts: float
    cost_inr: float
    #: True when the client explicitly asked for a regeneration of the previous answer.
    explicit_regenerate: bool = False
    #: Hold this request was created to resolve, if any.
    resolves_hold_id: str | None = None
    #: The hold this request *raised*, if any -- the other end of an escalation edge.
    raised_hold_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReworkEdge:
    """Child request attributed back to the parent that caused it."""

    child_request_id: str
    parent_request_id: str
    kind: str
    confidence: float
    inr_charged: float
    ts: float
    reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "child_request_id": self.child_request_id,
            "parent_request_id": self.parent_request_id,
            "kind": self.kind,
            "confidence": round(self.confidence, 4),
            "inr_charged": round(self.inr_charged, 4),
            "ts": self.ts,
            "reason": self.reason,
        }


@dataclass
class ReworkLedger:
    """Builds the session graph and totals the cost of answers that failed."""

    #: From the policy. A human looking at a held answer costs this whatever they decide.
    human_review_inr: float = 220.0
    retry_similarity: float = RETRY_SIMILARITY
    retry_window_s: float = RETRY_WINDOW_S
    edges: list[ReworkEdge] = field(default_factory=list)

    def attribute(self, turns: Sequence[SessionTurn]) -> list[ReworkEdge]:
        """Find every rework edge in one session's turns, in order."""
        ordered = sorted(turns, key=lambda turn: turn.ts)
        found: list[ReworkEdge] = []

        by_hold = {turn.raised_hold_id: turn for turn in ordered if turn.raised_hold_id}

        for index, turn in enumerate(ordered):
            previous = ordered[index - 1] if index else None

            # -- human escalation: the hold IS the edge, no inference ---------
            if turn.resolves_hold_id:
                parent = by_hold.get(turn.resolves_hold_id)
                if parent is not None and parent.request_id != turn.request_id:
                    found.append(
                        ReworkEdge(
                            child_request_id=turn.request_id,
                            parent_request_id=parent.request_id,
                            kind="human_escalation",
                            confidence=CONFIDENCE["human_escalation"],
                            # The reviewer's time is the dominant cost here, and it is
                            # charged whatever they decided -- an approval takes as long
                            # to read as a rejection.
                            inr_charged=turn.cost_inr + self.human_review_inr,
                            ts=turn.ts,
                            reason=f"resolved hold {turn.resolves_hold_id}",
                        )
                    )
                    continue

            if previous is None:
                continue

            # -- explicit regenerate ------------------------------------------
            if turn.explicit_regenerate:
                found.append(
                    ReworkEdge(
                        child_request_id=turn.request_id,
                        parent_request_id=previous.request_id,
                        kind="regenerate",
                        confidence=CONFIDENCE["regenerate"],
                        inr_charged=turn.cost_inr * CONFIDENCE["regenerate"],
                        ts=turn.ts,
                        reason="client requested a regeneration",
                    )
                )
                continue

            # -- inferred retry ------------------------------------------------
            elapsed = turn.ts - previous.ts
            if elapsed > self.retry_window_s or elapsed < 0:
                continue
            score = similarity(turn.question, previous.question)
            if score < self.retry_similarity:
                continue

            # Confidence scales with how far past the bar the similarity sits, from
            # RETRY_CONFIDENCE_FLOOR at the threshold to the ceiling at a verbatim
            # repeat. Attribution that MIGHT be wrong should be visibly might-be-wrong
            # in the money it moves.
            span = max(1e-9, 1.0 - self.retry_similarity)
            reach = min(1.0, (score - self.retry_similarity) / span)
            confidence = (
                RETRY_CONFIDENCE_FLOOR + (CONFIDENCE["retry"] - RETRY_CONFIDENCE_FLOOR) * reach
            )
            found.append(
                ReworkEdge(
                    child_request_id=turn.request_id,
                    parent_request_id=previous.request_id,
                    kind="retry",
                    confidence=confidence,
                    inr_charged=turn.cost_inr * confidence,
                    ts=turn.ts,
                    reason=f"re-asked at cosine {score:.3f} after {elapsed:.0f}s",
                )
            )

        self.edges.extend(found)
        return found

    # ------------------------------------------------------------------ #

    def total_rework_inr(self) -> float:
        return sum(edge.inr_charged for edge in self.edges)

    def by_parent(self) -> dict[str, float]:
        """How much rework each original answer went on to cause."""
        totals: dict[str, float] = {}
        for edge in self.edges:
            totals[edge.parent_request_id] = (
                totals.get(edge.parent_request_id, 0.0) + edge.inr_charged
            )
        return totals

    def report(self) -> dict[str, Any]:
        by_kind: dict[str, dict[str, float]] = {}
        for edge in self.edges:
            bucket = by_kind.setdefault(edge.kind, {"count": 0.0, "inr": 0.0})
            bucket["count"] += 1
            bucket["inr"] += edge.inr_charged

        inferred = [e for e in self.edges if e.kind == "retry"]
        notes: list[str] = []
        if inferred:
            mean_confidence = sum(e.confidence for e in inferred) / len(inferred)
            notes.append(
                f"{len(inferred)} of {len(self.edges)} edges are INFERRED retries at a "
                f"mean confidence of {mean_confidence:.2f}; their cost is charged at that "
                f"confidence, not in full"
            )
        return {
            "total_rework_inr": round(self.total_rework_inr(), 2),
            "edges": len(self.edges),
            "by_kind": {
                kind: {"count": int(v["count"]), "inr": round(v["inr"], 2)}
                for kind, v in sorted(by_kind.items())
            },
            "worst_parents": sorted(
                ({"request_id": k, "inr": round(v, 2)} for k, v in self.by_parent().items()),
                key=lambda row: -float(row["inr"]),
            )[:5],
            "notes": notes,
        }
