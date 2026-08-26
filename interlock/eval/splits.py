"""Provably disjoint calibration and evaluation splits.

D4-B6, and it is a correctness fix rather than a nicety.

Until this existed, calibration drew from all 45 corpus documents and the seeded eval set
drew from 43 of them. The *triples* differed — different seeds — so it looked fine, and
every reported number was quietly optimistic: the calibrator had read every document the
eval set was built from, and a fitted isotonic map that had learned one clause's phrasing
would score an eval item built from that same clause well for the wrong reason.

The plan is explicit about the standard: *the calibration set and the eval set must be
provably disjoint, and you must be able to say so.* "Different random seeds" is not that.
This partitions by **document**, so no passage that trained anything can appear in
anything measured.

**Split by hash, not by index.** ``d001…d027`` to calibration and the rest to eval would
correlate the split with document order, and the corpus is ordered by topic — every
contradictory pair sits adjacent. A hash of the doc id is stable across runs, independent
of ordering, and re-derivable by anyone with the manifest.

**Stratified by domain, and that was learned the hard way.** A plain hash split put every
``branch_info`` and ``fees`` document on the calibration side, so the eval set had no
low-stakes traffic at all: 128 of its 157 clean cases were ₹10,000+, the ₹0–100 band
disappeared, and the false-intervention rate read 100% because *every* eval case was
high-stakes. The number was measuring the split, not the system. Hashing within each
domain keeps both halves representative.

**The eval split gets what it needs, by construction.** Contradiction cases need two
documents in the same domain, and the untrusted documents belong on the eval side because
that is where the poisoned-document scenario is scored. Both are guaranteed here rather
than left to luck, and the guarantees are asserted rather than assumed.

**This makes the numbers worse, which is the point.** A metric that improves when you
close a leak was measuring the leak.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from interlock.retrieval.chunker import Chunk

__all__ = ["CorpusSplit", "split_corpus"]

#: Share of documents assigned to calibration. Calibration needs the larger half: it fits
#: isotonic maps and a probe, and both are hungrier than a 200-case eval set.
CALIBRATION_SHARE = 0.6

#: Documents that must land on the eval side whatever the hash says. The untrusted pair
#: is where the poisoned-document scenario is scored, and a calibrator that had trained
#: on the poison text would be scoring its own training data at eval time.
EVAL_PINNED: frozenset[str] = frozenset({"d044", "d045"})


def _bucket(doc_id: str) -> float:
    """Stable [0, 1) position for a document id.

    blake2b rather than ``hash()``: Python salts the latter per process, so the split
    would differ between the calibration run and the eval run — which is a leak in the
    opposite direction and much harder to notice.
    """
    digest = hashlib.blake2b(doc_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


@dataclass
class CorpusSplit:
    """Two disjoint sets of chunks, and the evidence that they are disjoint."""

    calibration: list[Chunk] = field(default_factory=list)
    evaluation: list[Chunk] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def calibration_docs(self) -> set[str]:
        return {chunk.doc_id for chunk in self.calibration}

    @property
    def evaluation_docs(self) -> set[str]:
        return {chunk.doc_id for chunk in self.evaluation}

    @property
    def disjoint(self) -> bool:
        return not (self.calibration_docs & self.evaluation_docs)

    def statement(self) -> str:
        """The sentence a reader is entitled to, with the numbers behind it."""
        overlap = self.calibration_docs & self.evaluation_docs
        if overlap:
            return (
                f"NOT DISJOINT: {len(overlap)} documents appear in both splits "
                f"({', '.join(sorted(overlap))}). Every metric measured across them is "
                f"optimistic by an unknown amount."
            )
        return (
            f"Provably disjoint: {len(self.calibration_docs)} documents for calibration, "
            f"{len(self.evaluation_docs)} for evaluation, zero shared. No passage that "
            f"trained anything appears in anything measured."
        )

    def report(self) -> dict[str, Any]:
        return {
            "disjoint": self.disjoint,
            "calibration_documents": sorted(self.calibration_docs),
            "evaluation_documents": sorted(self.evaluation_docs),
            "calibration_chunks": len(self.calibration),
            "evaluation_chunks": len(self.evaluation),
            "statement": self.statement(),
            "notes": self.notes,
        }


def split_corpus(
    chunks: list[Chunk],
    *,
    calibration_share: float = CALIBRATION_SHARE,
    eval_pinned: frozenset[str] = EVAL_PINNED,
) -> CorpusSplit:
    """Partition chunks by document into disjoint calibration and evaluation sets."""
    by_doc: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        by_doc[chunk.doc_id].append(chunk)

    notes: list[str] = []
    calibration_docs: set[str] = set()
    evaluation_docs: set[str] = set()

    # Stratify: hash WITHIN each domain, so both halves keep a representative mix. An
    # unstratified split silently handed the eval set only high-stakes documents, and
    # the metrics measured that rather than the system.
    by_domain: dict[str, list[str]] = defaultdict(list)
    for doc_id in sorted(by_doc):
        by_domain[by_doc[doc_id][0].domain].append(doc_id)

    for domain, doc_ids in sorted(by_domain.items()):
        free = [d for d in doc_ids if d not in eval_pinned]
        evaluation_docs.update(d for d in doc_ids if d in eval_pinned)
        # Sorted by hash rather than thresholded on it: with two or three documents in a
        # domain, an independent coin flip per document routinely sends all of them the
        # same way, which is the failure this stratification exists to prevent.
        ranked = sorted(free, key=_bucket)
        cut = round(len(ranked) * calibration_share)
        # Never starve a domain entirely: a domain wholly absent from one half is the
        # unstratified failure again, one level down.
        if len(ranked) >= 2:
            cut = max(1, min(len(ranked) - 1, cut))
        calibration_docs.update(ranked[:cut])
        evaluation_docs.update(ranked[cut:])
        if len(ranked) == 1:
            notes.append(
                f"domain '{domain}' has one document; it went to "
                f"{'calibration' if cut else 'evaluation'} and the other half has none"
            )

    # The eval set builds contradiction cases from two documents in the SAME domain. If
    # the hash left a domain with only one eval document, that mode cannot be built --
    # and it would fail quietly, as a category that silently shrank rather than an error.
    eval_domains: dict[str, set[str]] = defaultdict(set)
    for doc_id in evaluation_docs:
        eval_domains[by_doc[doc_id][0].domain].add(doc_id)
    if not any(len(docs) >= 2 for docs in eval_domains.values()):
        # Move the largest calibration domain across rather than failing: a split with no
        # contradiction cases measures five of the six failure modes and says six.
        by_domain: dict[str, set[str]] = defaultdict(set)
        for doc_id in calibration_docs:
            by_domain[by_doc[doc_id][0].domain].add(doc_id)
        donor = max(by_domain.values(), key=len, default=set())
        moved = set(sorted(donor)[:2])
        calibration_docs -= moved
        evaluation_docs |= moved
        notes.append(
            f"moved {sorted(moved)} to the eval split: no eval domain had the two "
            f"documents a contradiction case needs"
        )

    split = CorpusSplit(
        calibration=[c for doc in sorted(calibration_docs) for c in by_doc[doc]],
        evaluation=[c for doc in sorted(evaluation_docs) for c in by_doc[doc]],
        notes=notes,
    )
    if not split.disjoint:  # pragma: no cover - structurally impossible above
        raise AssertionError(split.statement())
    return split
