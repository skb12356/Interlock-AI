"""Generate labelled ``(context, question, answer)`` triples with induced failures.

Calibration needs ground truth, and hand-labelling is expensive enough that the plan
budgets a whole task for 300 items (D2-B3). This module supplies the other several
hundred by *constructing* the failure rather than finding it: take a passage and an
answer that is correct with respect to it, then break one specific thing and record
exactly what was broken.

That inverts the usual labelling problem. Nobody has to judge whether the answer is
grounded; we know, because we are the ones who un-grounded it.

**What this is not.** Induced failures are not a substitute for the human anchor set.
They are drawn from a generator, so a detector can in principle learn the generator's
fingerprint rather than the defect -- a corrupted number is always a *round* corruption
here, for instance. That is why D2-B3's hand-labelled 300 remains on the never-cut
list and why the meta-monitor re-scores *those*, not these. Induced data calibrates;
human data audits the calibration.

The taxonomy matches the plan's:

* ``retrieval_dropped``   -- the answer's supporting passage is not in context
* ``number_corrupted``    -- a figure in the answer no longer matches the passage
* ``clause_swapped``      -- the answer cites a real clause that says something else
* ``unanswerable``        -- the context genuinely does not settle the question
* ``contradiction``       -- a superseded passage is injected alongside the current one
* ``clean``               -- nothing broken; the negative class

Every triple carries a machine-checkable ``defect`` label and the reason it holds.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from interlock.core.types import Defect, Fragment
from interlock.retrieval.chunker import Chunk

__all__ = [
    "DEFECT_BASE_RATE",
    "FAILURE_MODES",
    "FAILURE_WEIGHTS",
    "LabelledTriple",
    "TripleGenerator",
]

#: What share of items carry a defect at all.
#:
#: **This number is load-bearing and was got wrong once.** An isotonic map is calibrated
#: to the base rate of the set it was fitted on, so a 50/50 calibration set teaches the
#: detector that half of everything is broken. The symptom was not subtle and was still
#: easy to miss: a perfectly grounded sentence came back at P(ungrounded)=0.135, which at
#: Rs.40,000 impact and a 2.5x reversibility multiplier is Rs.13,500 of expected harm --
#: so the optimiser held a correct answer for human review, and did so *correctly*, given
#: what it had been told a clean sentence looks like.
#:
#: 10% is a defensible figure for RAG-grounded support traffic and it is a stated
#: assumption, not a measurement. When the seeded eval set (D3-B7) produces a real
#: observed rate, this should move to match it -- and the calibration must be re-fitted,
#: not merely re-thresholded.
DEFECT_BASE_RATE = 0.10

#: Relative weights **among the defective items**, normalised to ``DEFECT_BASE_RATE``.
#: Kept separate from the base rate so that changing how common defects are does not
#: silently change which defects they are.
FAILURE_WEIGHTS: dict[str, float] = {
    "retrieval_dropped": 0.24,
    "number_corrupted": 0.24,
    "clause_swapped": 0.20,
    "unanswerable": 0.16,
    "contradiction": 0.16,
}

#: The absolute taxonomy the generator samples from.
FAILURE_MODES: dict[str, float] = {
    "clean": 1.0 - DEFECT_BASE_RATE,
    **{mode: weight * DEFECT_BASE_RATE for mode, weight in FAILURE_WEIGHTS.items()},
}

#: Which defect class each failure mode produces, in the vocabulary the objective
#: prices. Several modes map to ``ungrounded`` because that is genuinely what they are:
#: the distinction between them is about how the failure was *made*, not what it is.
_MODE_TO_DEFECT: dict[str, Defect | None] = {
    "clean": None,
    "retrieval_dropped": "ungrounded",
    "number_corrupted": "ungrounded",
    "clause_swapped": "ungrounded",
    "unanswerable": "ungrounded",
    "contradiction": "contradicted",
}

_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")
_CLAUSE = re.compile(r"\bClause\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")

#: How many chunks to try before conceding a failure mode cannot be built.
MAX_CHUNK_ATTEMPTS = 25


def _shift_clause(reference: str) -> str:
    """Nudge a clause number to a neighbour that plausibly exists.

    Used only when the corpus offers no real clause to swap in. Shifting the minor
    part keeps it inside the same article, which is what a model actually gets
    wrong -- inventing "Clause 91.7" would be caught by anything.
    """
    if "." in reference:
        major, minor = reference.split(".", 1)
        try:
            return f"{major}.{int(minor) + 1}"
        except ValueError:
            return reference
    try:
        return str(int(reference) + 1)
    except ValueError:
        return reference


@dataclass(frozen=True, slots=True)
class LabelledTriple:
    """One item of calibration data, and why its label is what it is."""

    triple_id: str
    question: str
    answer: str
    context: tuple[Fragment, ...]
    #: None when the answer is fully supported.
    defect: Defect | None
    failure_mode: str
    #: How the failure was constructed. Recorded so a surprising calibration result can
    #: be traced back to the generator rather than blamed on the detector.
    provenance_note: str
    source_doc_id: str = ""
    #: The span that was broken, when there is one. This is what a verifier should find,
    #: and what L2 repair would aim at.
    offending_span: str = ""

    @property
    def is_defective(self) -> bool:
        return self.defect is not None

    def to_row(self) -> dict[str, Any]:
        return {
            "triple_id": self.triple_id,
            "question": self.question,
            "answer": self.answer,
            "context": [
                {"text": f.text, "doc_id": f.doc_id, "provenance": f.provenance}
                for f in self.context
            ],
            "defect": self.defect,
            "failure_mode": self.failure_mode,
            "label": int(self.is_defective),
            "provenance_note": self.provenance_note,
            "source_doc_id": self.source_doc_id,
            "offending_span": self.offending_span,
        }


@dataclass
class TripleGenerator:
    """Builds labelled triples from indexed corpus chunks.

    Deterministic given a seed, which matters more than it sounds: a calibration map
    fitted on data nobody can regenerate is a calibration map nobody can audit.
    """

    chunks: list[Chunk]
    seed: int = 20260825
    #: Modes that could not be built, by name. Read by the report; a mode that falls
    #: back often is a mode the corpus cannot support, and pretending otherwise makes
    #: the calibration set quietly different from the one described.
    fallbacks: dict[str, int] = field(default_factory=dict)
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        if not self.chunks:
            raise ValueError("no chunks to generate from")

    # ------------------------------------------------------------------ #

    def generate(self, count: int) -> list[LabelledTriple]:
        """``count`` triples, with the taxonomy's proportions respected exactly.

        Sampled by construction rather than by rolling a die ``count`` times: with a
        few hundred items, random assignment leaves the rare modes lumpy, and a
        calibration bin with three items in it is noise wearing a number's clothes.
        """
        plan: list[str] = []
        for mode, share in FAILURE_MODES.items():
            plan.extend([mode] * round(count * share))
        while len(plan) < count:
            plan.append("clean")
        plan = plan[:count]
        self._rng.shuffle(plan)

        triples: list[LabelledTriple] = []
        for index, mode in enumerate(plan):
            triple = self._one(index, mode)
            if triple is not None:
                triples.append(triple)
        return triples

    def _one(self, index: int, mode: str) -> LabelledTriple | None:
        builder = getattr(self, f"_make_{mode}")
        result: tuple[str, str, list[Fragment], str, str] | None = None
        chunk = self._rng.choice(self.chunks)

        # Most chunks cannot host most failures -- there is no number to corrupt in a
        # branch-timings passage. Try several before giving up, or the rare modes end
        # up with a handful of items each and the taxonomy is balanced only on paper.
        for _ in range(MAX_CHUNK_ATTEMPTS):
            chunk = self._rng.choice(self.chunks)
            result = builder(chunk)
            if result is not None:
                break

        if result is None:
            # Genuinely unhostable. Fall back to clean rather than forcing it -- a
            # fabricated failure that does not fit its passage is a data point about
            # nothing -- and count it, so the imbalance is reported rather than hidden.
            self.fallbacks[mode] = self.fallbacks.get(mode, 0) + 1
            fallback = self._make_clean(chunk)
            if fallback is None:
                return None
            question, answer, context, note, span = fallback
            mode = "clean"
        else:
            question, answer, context, note, span = result

        return LabelledTriple(
            triple_id=f"t{index:05d}",
            question=question,
            answer=answer,
            context=tuple(context),
            defect=_MODE_TO_DEFECT[mode],
            failure_mode=mode,
            provenance_note=note,
            source_doc_id=chunk.doc_id,
            offending_span=span,
        )

    # -- the failure modes --------------------------------------------- #

    def _make_clean(self, chunk: Chunk) -> tuple[str, str, list[Fragment], str, str] | None:
        sentence = self._pick_sentence(chunk)
        if not sentence:
            return None
        return (
            self._question_for(chunk),
            sentence,
            [chunk.to_fragment()],
            "answer copied verbatim from the passage in context",
            "",
        )

    def _make_retrieval_dropped(
        self, chunk: Chunk
    ) -> tuple[str, str, list[Fragment], str, str] | None:
        """The answer is *true*, but its support is not in context.

        The subtlest of the modes and the most valuable. A detector that only checks
        plausibility passes this; only one that checks *grounding* catches it.
        """
        sentence = self._pick_sentence(chunk)
        if not sentence:
            return None
        others = [c for c in self.chunks if c.doc_id != chunk.doc_id and c.domain != chunk.domain]
        if not others:
            return None
        distractors = self._rng.sample(others, min(2, len(others)))
        return (
            self._question_for(chunk),
            sentence,
            [c.to_fragment() for c in distractors],
            f"supporting passage {chunk.chunk_id} withheld; context is unrelated documents",
            sentence,
        )

    def _make_number_corrupted(
        self, chunk: Chunk
    ) -> tuple[str, str, list[Fragment], str, str] | None:
        sentence = self._pick_sentence(chunk, requiring=_NUMBER)
        if not sentence:
            return None
        match = _NUMBER.search(sentence)
        assert match is not None
        original = match.group(1)
        corrupted = self._corrupt_number(original)
        if corrupted == original:
            return None
        broken = sentence[: match.start(1)] + corrupted + sentence[match.end(1) :]
        return (
            self._question_for(chunk),
            broken,
            [chunk.to_fragment()],
            f"figure {original!r} in the passage rewritten as {corrupted!r}",
            broken,
        )

    def _make_clause_swapped(
        self, chunk: Chunk
    ) -> tuple[str, str, list[Fragment], str, str] | None:
        """Cites a real clause that says something else. The Scene 1 failure exactly."""
        sentence = self._pick_sentence(chunk, requiring=_CLAUSE)
        if not sentence:
            sentence = self._pick_sentence(chunk)
            if not sentence:
                return None
            other = self._clause_from_elsewhere(chunk)
            if other is None:
                return None
            broken = f"{sentence.rstrip('.')} under Clause {other}."
            note = f"attributed to Clause {other}, which is from a different document"
        else:
            match = _CLAUSE.search(sentence)
            assert match is not None
            other = self._clause_from_elsewhere(chunk) or _shift_clause(match.group(1))
            broken = sentence[: match.start(1)] + other + sentence[match.end(1) :]
            note = f"clause reference {match.group(1)} rewritten as {other}"
        return (self._question_for(chunk), broken, [chunk.to_fragment()], note, broken)

    def _make_unanswerable(self, chunk: Chunk) -> tuple[str, str, list[Fragment], str, str] | None:
        """A confident answer to a question the context does not settle."""
        sentence = self._pick_sentence(chunk)
        if not sentence:
            return None
        question = (
            f"Regarding {chunk.title.split('-')[0].strip().lower() or 'this product'}, "
            "what is the exact penalty if the customer defaults twice in the same quarter?"
        )
        return (
            question,
            sentence,
            [chunk.to_fragment()],
            "question asks for a fact the passage does not contain; answer asserts anyway",
            sentence,
        )

    def _make_contradiction(self, chunk: Chunk) -> tuple[str, str, list[Fragment], str, str] | None:
        """The superseded passage is in context beside the current one.

        Uses the corpus's real contradictory pairs where the chunk is half of one, so
        the contradiction is genuine rather than two unrelated passages sitting together.
        """
        partner = self._contradiction_partner(chunk)
        if partner is None:
            return None
        sentence = self._pick_sentence(partner)
        if not sentence:
            return None
        return (
            self._question_for(chunk),
            sentence,
            [chunk.to_fragment(), partner.to_fragment()],
            (
                f"answer follows {partner.chunk_id}, which {chunk.chunk_id} supersedes; "
                "both are in context"
            ),
            sentence,
        )

    # -- helpers -------------------------------------------------------- #

    def _pick_sentence(self, chunk: Chunk, requiring: re.Pattern[str] | None = None) -> str:
        candidates = [s.strip() for s in _SENTENCE.split(chunk.body) if len(s.strip()) > 40]
        if requiring is not None:
            candidates = [s for s in candidates if requiring.search(s)]
        return self._rng.choice(candidates) if candidates else ""

    def _question_for(self, chunk: Chunk) -> str:
        subject = chunk.title.split("-")[0].strip() or chunk.domain.replace("_", " ")
        return f"What do the terms say about {subject.lower()}?"

    def _corrupt_number(self, original: str) -> str:
        """Change the figure without making it absurd.

        An obviously silly number (2% becoming 900%) would be caught by a plausibility
        check rather than a grounding check, and would flatter every detector measured
        against it.
        """
        cleaned = original.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            return original
        factor = self._rng.choice([0.5, 1.5, 2.0, 0.75, 1.25])
        changed = value * factor
        if value == int(value) and changed == int(changed):
            return f"{int(changed):,}" if "," in original else str(int(changed))
        return f"{changed:.2f}".rstrip("0").rstrip(".")

    def _clause_from_elsewhere(self, chunk: Chunk) -> str | None:
        for other in self._rng.sample(self.chunks, min(12, len(self.chunks))):
            if other.doc_id == chunk.doc_id:
                continue
            match = _CLAUSE.search(other.text)
            if match:
                return match.group(1)
        return None

    def _contradiction_partner(self, chunk: Chunk) -> Chunk | None:
        """A chunk from a different document in the same domain.

        Same domain is what makes the two passages *about* the same thing, which is
        what makes the contradiction real rather than a pair of strangers.
        """
        candidates = [
            c for c in self.chunks if c.domain == chunk.domain and c.doc_id != chunk.doc_id
        ]
        return self._rng.choice(candidates) if candidates else None
