"""Deterministic construction of the 300-item grounding audit anchor."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from interlock.eval.induce import LabelledTriple, TripleGenerator
from interlock.retrieval.chunker import Chunk

__all__ = [
    "ANCHOR_FAILURE_MODES",
    "ANCHOR_MODE_COUNTS",
    "CHALLENGE_LEVELS",
    "AnchorTriple",
    "ChallengeLevel",
    "build_anchor",
]

ANCHOR_FAILURE_MODES: tuple[str, ...] = (
    "retrieval_dropped",
    "number_corrupted",
    "clause_swapped",
    "unanswerable",
    "contradiction",
)
ANCHOR_MODE_COUNTS: dict[str, int] = {
    "clean": 200,
    **{mode: 20 for mode in ANCHOR_FAILURE_MODES},
}

ChallengeLevel = Literal["L1_direct", "L2_distractor", "L3_conflict"]
CHALLENGE_LEVELS: tuple[ChallengeLevel, ...] = (
    "L1_direct",
    "L2_distractor",
    "L3_conflict",
)


@dataclass(frozen=True, slots=True)
class AnchorTriple:
    """A labelled triple plus the audit slice used to interpret it."""

    triple: LabelledTriple
    challenge_level: ChallengeLevel
    domain: str


def _level_plan(count: int) -> list[ChallengeLevel]:
    per_level, remainder = divmod(count, len(CHALLENGE_LEVELS))
    return [
        level
        for index, level in enumerate(CHALLENGE_LEVELS)
        for _ in range(per_level + int(index < remainder))
    ]


def _document_id(fragment_doc_id: str | None) -> str:
    return fragment_doc_id.split("#", 1)[0] if fragment_doc_id else ""


def _enrich_context(
    triple: LabelledTriple,
    level: ChallengeLevel,
    *,
    chunks: Sequence[Chunk],
    rng: random.Random,
    source_domain: str,
) -> LabelledTriple:
    added_count = {
        "L1_direct": 0,
        "L2_distractor": 1,
        "L3_conflict": 2,
    }[level]
    if added_count == 0:
        return triple

    excluded_docs = {_document_id(fragment.doc_id) for fragment in triple.context}
    excluded_docs.add(triple.source_doc_id)
    candidates = [
        chunk
        for chunk in chunks
        if chunk.provenance == "retrieved_verified"
        and chunk.doc_id not in excluded_docs
        and chunk.domain != source_domain
    ]
    candidate_docs = sorted({chunk.doc_id for chunk in candidates})
    if len(candidate_docs) < added_count:
        raise ValueError(f"not enough safe distractors for {triple.triple_id} at {level}")
    selected_docs = rng.sample(candidate_docs, added_count)
    by_doc = {chunk.doc_id: chunk for chunk in candidates}
    enriched = (*triple.context, *(by_doc[doc_id].to_fragment() for doc_id in selected_docs))
    return LabelledTriple(
        triple_id=triple.triple_id,
        question=triple.question,
        answer=triple.answer,
        context=enriched,
        defect=triple.defect,
        failure_mode=triple.failure_mode,
        provenance_note=triple.provenance_note,
        source_doc_id=triple.source_doc_id,
        offending_span=triple.offending_span,
    )


def build_anchor(chunks: Sequence[Chunk], *, seed: int) -> list[AnchorTriple]:
    """Build the approved mode/level matrix without changing calibration priors."""
    chunk_list = list(chunks)
    triples = TripleGenerator(chunks=chunk_list, seed=seed).generate_exact(ANCHOR_MODE_COUNTS)
    levels_by_mode = {mode: _level_plan(count) for mode, count in ANCHOR_MODE_COUNTS.items()}
    mode_indices: defaultdict[str, int] = defaultdict(int)
    domains = {chunk.doc_id: chunk.domain for chunk in chunk_list}
    rng = random.Random(seed)
    anchors: list[AnchorTriple] = []
    for triple in triples:
        mode_index = mode_indices[triple.failure_mode]
        level = levels_by_mode[triple.failure_mode][mode_index]
        mode_indices[triple.failure_mode] += 1
        try:
            domain = domains[triple.source_doc_id]
        except KeyError as exc:
            raise ValueError(f"source document missing for {triple.triple_id}") from exc
        anchors.append(
            AnchorTriple(
                triple=_enrich_context(
                    triple,
                    level,
                    chunks=chunk_list,
                    rng=rng,
                    source_domain=domain,
                ),
                challenge_level=level,
                domain=domain,
            )
        )
    return anchors
