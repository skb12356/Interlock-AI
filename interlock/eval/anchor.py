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

_QUESTION_TEMPLATES: tuple[str, ...] = (
    "What does {title} state?",
    "Which rule is set out in {title}?",
    "According to {title}, what applies?",
    "How does {title} describe the applicable terms?",
    "What requirement does {title} establish?",
    "Which policy detail is specified by {title}?",
    "Under {title}, what condition governs the customer?",
    "What does the cited policy say in {title}?",
)

_UNANSWERABLE_QUESTION_TEMPLATES: tuple[str, ...] = (
    "Under {title}, what exact penalty applies after two defaults in one quarter?",
    "What precise charge does {title} impose for two defaults in the same quarter?",
    "If a customer defaults twice in one quarter, what exact penalty does {title} set?",
    "Which specific penalty follows two same-quarter defaults under {title}?",
    "How much is the exact two-default quarterly penalty in {title}?",
    "What fixed sanction does {title} prescribe for a second quarterly default?",
    "According to {title}, what precise consequence follows two defaults in a quarter?",
    "What exact amount is charged for two defaults in one quarter under {title}?",
)

# A cell is small (67 rows at most), while the calibration corpus offers thousands of
# source/answer/context combinations once distractors are considered. Keep retrying
# collisions, but never hang if a reduced corpus genuinely cannot satisfy a quota.
MAX_ANCHOR_CELL_ATTEMPTS = 20_000


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


def _vary_question(triple: LabelledTriple, *, title: str, occurrence: int) -> LabelledTriple:
    templates = (
        _UNANSWERABLE_QUESTION_TEMPLATES
        if triple.failure_mode == "unanswerable"
        else _QUESTION_TEMPLATES
    )
    return LabelledTriple(
        triple_id=triple.triple_id,
        question=templates[occurrence % len(templates)].format(title=title),
        answer=triple.answer,
        context=triple.context,
        defect=triple.defect,
        failure_mode=triple.failure_mode,
        provenance_note=triple.provenance_note,
        source_doc_id=triple.source_doc_id,
        offending_span=triple.offending_span,
    )


def _grounding_signature(anchor: AnchorTriple) -> tuple[object, ...]:
    triple = anchor.triple
    return (
        triple.answer,
        tuple((fragment.text, fragment.doc_id, fragment.provenance) for fragment in triple.context),
        triple.defect,
        triple.failure_mode,
        anchor.challenge_level,
    )


def _with_triple_id(anchor: AnchorTriple, triple_id: str) -> AnchorTriple:
    triple = anchor.triple
    return AnchorTriple(
        triple=LabelledTriple(
            triple_id=triple_id,
            question=triple.question,
            answer=triple.answer,
            context=triple.context,
            defect=triple.defect,
            failure_mode=triple.failure_mode,
            provenance_note=triple.provenance_note,
            source_doc_id=triple.source_doc_id,
            offending_span=triple.offending_span,
        ),
        challenge_level=anchor.challenge_level,
        domain=anchor.domain,
    )


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
    generator = TripleGenerator(chunks=chunk_list, seed=seed)
    domains = {chunk.doc_id: chunk.domain for chunk in chunk_list}
    titles = {chunk.doc_id: chunk.title for chunk in chunk_list}
    question_occurrences: defaultdict[tuple[object, ...], int] = defaultdict(int)
    rng = random.Random(seed)
    anchors: list[AnchorTriple] = []
    signatures: set[tuple[object, ...]] = set()
    for mode, count in ANCHOR_MODE_COUNTS.items():
        level_plan = _level_plan(count)
        for level in CHALLENGE_LEVELS:
            quota = level_plan.count(level)
            cell: list[AnchorTriple] = []
            for _ in range(MAX_ANCHOR_CELL_ATTEMPTS):
                if len(cell) == quota:
                    break
                try:
                    triple = generator.generate_exact({mode: 1})[0]
                except ValueError:
                    continue
                try:
                    domain = domains[triple.source_doc_id]
                except KeyError as exc:
                    raise ValueError(f"source document missing for {triple.triple_id}") from exc
                enriched = _enrich_context(
                    triple,
                    level,
                    chunks=chunk_list,
                    rng=rng,
                    source_domain=domain,
                )
                candidate = AnchorTriple(
                    triple=enriched,
                    challenge_level=level,
                    domain=domain,
                )
                signature = _grounding_signature(candidate)
                if signature in signatures:
                    continue
                question_key = (
                    enriched.failure_mode,
                    enriched.source_doc_id,
                )
                occurrence = question_occurrences[question_key]
                question_occurrences[question_key] += 1
                candidate = AnchorTriple(
                    triple=_vary_question(
                        enriched,
                        title=titles[enriched.source_doc_id],
                        occurrence=occurrence,
                    ),
                    challenge_level=level,
                    domain=domain,
                )
                signatures.add(signature)
                cell.append(candidate)
            if len(cell) != quota:
                raise ValueError(
                    f"cannot satisfy independent anchor quota for {mode}/{level}: "
                    f"built {len(cell)} of {quota} after {MAX_ANCHOR_CELL_ATTEMPTS} attempts"
                )
            anchors.extend(cell)
    rng.shuffle(anchors)
    return [_with_triple_id(anchor, f"t{index:05d}") for index, anchor in enumerate(anchors)]
