"""The semantic cache: serve a previous answer, but only when it is safe to.

A cache hit is the cheapest possible request — no generation, no verification, near-zero
latency. It is also the easiest place in the system to be quietly wrong, because a
near-miss produces a confident answer to a question nobody asked.

So the plan specifies four conditions, **all** of which must hold:

1. **Semantic similarity ≥ 0.95.** Not "similar" — nearly the same question.
2. **The retrieval context hash matches.** This is the one people leave out, and it is
   the one that matters most. The corpus changes: a clause is superseded, a rate card
   expires, a document is re-uploaded. An answer that was correct against last month's
   context is *wrong now*, and it will look perfectly plausible. Keying on the question
   alone means the cache keeps serving the old answer after the policy changed.
3. **Stakes at or below a threshold.** A ₹40,000 loan answer is regenerated and
   re-verified every time. The saving is not worth the chance that something moved.
4. **The cached answer previously passed verification.** Caching an answer that was
   repaired, held or blocked would replay a defect on every subsequent hit — turning one
   bad answer into a permanent one, at machine speed.

Conjunctive on purpose. Each condition rules out a different failure, and any three of
them still admit the fourth's. Together they make a hit rare and safe; the plan's own
guidance is to assume the conservative end of published hit rates (20–45%), and this is
stricter than most of what those numbers were measured on.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from interlock.core.types import Fragment

__all__ = ["CacheEntry", "CacheLookup", "SemanticCache", "context_hash"]

#: How near a question must be. High enough that "what is the fee" and "what is the fee
#: for premium accounts" do not collide -- a threshold that admitted that would answer
#: the second with the first, confidently.
SIMILARITY_THRESHOLD = 0.95

#: Stakes at or below which a hit may be served. Defaults to the buffering threshold, so
#: "low stakes" means one thing across routing, buffering and caching.
DEFAULT_MAX_STAKES_INR = 1_000.0

#: Entries kept. Small on purpose: this is an in-process dict, and the plan's no-Redis
#: rule means a cache that grew without bound would grow the gateway's memory instead.
DEFAULT_CAPACITY = 512

#: Actions after which an answer may be cached. Anything else means the answer needed
#: work, and replaying it would make one defect permanent.
CACHEABLE_ACTIONS = frozenset({"L0_pass"})


def context_hash(retrieved: Sequence[Fragment]) -> str:
    """A stable digest of the retrieved context.

    Over ``doc_id`` **and text**, not doc_id alone: a document can be re-uploaded with
    the same identifier and different contents, which is exactly the supersession case
    this guard exists to catch. Sorted, so retrieval returning the same passages in a
    different order is still a hit.
    """
    digest = hashlib.sha256()
    for item in sorted(
        f"{fragment.doc_id or ''}|{fragment.text}" for fragment in retrieved
    ):
        digest.update(item.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One previously-served answer, and everything needed to decide it is still valid."""

    question: str
    answer: str
    embedding: tuple[float, ...]
    context_digest: str
    stakes_inr: float
    action: str
    model: str
    policy_version: str
    stored_ts: float = 0.0


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """A hit, or a miss with the reason. The reason is the useful part.

    "Cache miss" tells an operator nothing. "Missed because the context hash changed"
    tells them a document was re-uploaded, which is a different and much more
    interesting fact.
    """

    entry: CacheEntry | None
    similarity: float = 0.0
    reason: str = ""

    @property
    def hit(self) -> bool:
        return self.entry is not None


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_left * norm_right)))


@dataclass
class SemanticCache:
    """LRU over answers, with the four conditions enforced on every lookup."""

    similarity_threshold: float = SIMILARITY_THRESHOLD
    max_stakes_inr: float = DEFAULT_MAX_STAKES_INR
    capacity: int = DEFAULT_CAPACITY
    #: Stamped on every entry; a policy change invalidates the whole cache, because the
    #: policy is what decided the cached answer was acceptable in the first place.
    policy_version: str = ""
    _entries: OrderedDict[str, CacheEntry] = field(default_factory=OrderedDict, init=False)
    hits: int = field(default=0, init=False)
    misses: int = field(default=0, init=False)
    miss_reasons: dict[str, int] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------ #

    def lookup(
        self,
        *,
        question: str,
        embedding: Sequence[float],
        retrieved: Sequence[Fragment],
        stakes_inr: float,
    ) -> CacheLookup:
        """All four conditions, in the order that fails cheapest first."""
        if stakes_inr > self.max_stakes_inr:
            return self._miss(
                f"stakes Rs.{stakes_inr:,.0f} above the Rs.{self.max_stakes_inr:,.0f} "
                f"cache ceiling; regenerated and re-verified"
            )

        digest = context_hash(retrieved)
        best: CacheEntry | None = None
        best_similarity = 0.0
        context_mismatch = False

        for entry in self._entries.values():
            similarity = cosine(embedding, entry.embedding)
            if similarity < self.similarity_threshold:
                continue
            if similarity <= best_similarity:
                continue
            if entry.context_digest != digest:
                # Near-identical question, different evidence. Recorded separately: this
                # is the supersession case, and reporting it as a plain miss would hide
                # the fact that a document changed.
                context_mismatch = True
                continue
            if entry.policy_version != self.policy_version:
                continue
            best, best_similarity = entry, similarity

        if best is None:
            if context_mismatch:
                return self._miss(
                    "question matched but the retrieved context has changed -- a document "
                    "was superseded or re-uploaded, so the cached answer is stale"
                )
            return self._miss("no sufficiently similar question")

        self._entries.move_to_end(next(k for k, v in self._entries.items() if v is best))
        self.hits += 1
        return CacheLookup(entry=best, similarity=best_similarity, reason="hit")

    def store(
        self,
        *,
        question: str,
        answer: str,
        embedding: Sequence[float],
        retrieved: Sequence[Fragment],
        stakes_inr: float,
        action: str,
        model: str,
        ts: float = 0.0,
    ) -> bool:
        """Store, if the answer earned it. Returns whether it was stored.

        The action check is the fourth condition and the one with teeth: caching an
        answer that was repaired, held or blocked would replay a defect on every
        subsequent hit, turning one bad answer into a permanent one at machine speed.
        """
        if action not in CACHEABLE_ACTIONS:
            return False
        if stakes_inr > self.max_stakes_inr:
            return False
        if not embedding:
            return False

        key = hashlib.sha256(f"{question}|{context_hash(retrieved)}".encode()).hexdigest()[:32]
        self._entries[key] = CacheEntry(
            question=question,
            answer=answer,
            embedding=tuple(float(value) for value in embedding),
            context_digest=context_hash(retrieved),
            stakes_inr=stakes_inr,
            action=action,
            model=model,
            policy_version=self.policy_version,
            stored_ts=ts,
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return True

    # ------------------------------------------------------------------ #

    def _miss(self, reason: str) -> CacheLookup:
        self.misses += 1
        # Bucketed by the leading clause so the counter stays readable rather than
        # accumulating one key per distinct rupee amount.
        bucket = reason.split(";")[0].split("--")[0].strip()[:60]
        self.miss_reasons[bucket] = self.miss_reasons.get(bucket, 0) + 1
        return CacheLookup(entry=None, reason=reason)

    def invalidate_all(self, reason: str = "") -> int:
        """Drop everything. Used when the policy or the corpus changes."""
        count = len(self._entries)
        self._entries.clear()
        return count

    def __len__(self) -> int:
        return len(self._entries)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "capacity": self.capacity,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "miss_reasons": dict(sorted(self.miss_reasons.items(), key=lambda kv: -kv[1])),
            "similarity_threshold": self.similarity_threshold,
            "max_stakes_inr": self.max_stakes_inr,
            # Stated because the plan asks for the conservative end of published ranges
            # to be assumed, and this cache is stricter than most of what those numbers
            # were measured on.
            "note": (
                "four conjunctive conditions: similarity, context hash, stakes ceiling, "
                "and the cached answer having passed verification"
            ),
        }
