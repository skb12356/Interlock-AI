"""Deterministic grounding signals: is this sentence supported by what we retrieved?

These are the signals the calibration layer has to work with before the observer probe
exists, and they are **weak on purpose rather than by accident**. Each one is a cheap,
provably-correct check of the kind CLAUDE.md s3 prefers over a clever probabilistic one:
a number in the answer that appears nowhere in the context is unsupported, full stop, no
model required.

What they are not is *sufficient*. Lexical overlap cannot tell a correct paraphrase from
a fabrication that reuses the passage's vocabulary, and it never will. That is what the
observer probe (D2-B4/B7) and the MiniCheck-class verifier (D2-B6) are for. The
architecture that matters here is that all of them -- these, the probe, the verifier --
are ``SignalReading`` objects that flow through the same isotonic calibration and the
same conformal threshold. Adding the probe later changes the inputs, not the pipeline.

Every signal returns a **raw score in [0, 1] where higher means more suspicious**, and
none of them is a probability until calibration says so (CLAUDE.md s3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from interlock.core.types import Fragment, SignalReading

__all__ = [
    "GROUNDING_SIGNALS",
    "citation_unsupported",
    "context_conflict",
    "grounding_signals",
    "hedge_density",
    "is_claim_bearing",
    "numeric_unsupported",
    "question_drift",
    "unsupported_content",
]

_WORD = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")
_CLAUSE = re.compile(r"\b(?:clause|section|article)\s+(\d+(?:\.\d+)?)", re.IGNORECASE)

#: Function words carry no grounding information -- an answer and a passage will always
#: share "the of a to", and counting that as support inflates every score toward
#: "grounded" and flattens the signal's discrimination to nothing.
_STOPWORDS = frozenset(
    (
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "shall",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    )
)

#: Words that mark an answer as appropriately uncertain. Their *absence* is what makes a
#: wrong answer dangerous: "confidence is the instruction humans follow".
_HEDGES = frozenset(
    (
        "about",
        "appears",
        "approximately",
        "around",
        "cannot",
        "confirm",
        "could",
        "depending",
        "estimated",
        "generally",
        "likely",
        "may",
        "might",
        "often",
        "please",
        "possibly",
        "roughly",
        "seems",
        "subject",
        "typically",
        "unable",
        "unclear",
        "unlikely",
        "usually",
    )
)

#: The names these emit, in the order the fusion layer expects them.
GROUNDING_SIGNALS: tuple[str, ...] = (
    "grounding.unsupported_content",
    "grounding.numeric_unsupported",
    "grounding.citation_unsupported",
    "grounding.overconfidence",
    "grounding.context_conflict",
    "grounding.question_drift",
)


#: Openers that mark a sentence as *procedural* rather than factual: the assistant
#: narrating what it is doing, not asserting anything about the documents.
_PROCEDURAL = (
    "let me",
    "i will",
    "i'll",
    "i am going to",
    "i'm going to",
    "i have reviewed",
    "i've reviewed",
    "i can help",
    "i'd be happy",
    "i would be happy",
    "let us",
    "checking",
    "searching",
    "one moment",
    "please hold",
    "thank you",
    "sure,",
    "certainly",
    "of course",
    "happy to help",
    "i understand",
)

#: Sentences shorter than this, with no figure and no clause reference, are almost
#: always acknowledgements ("Understood.", "Here you go.").
_MIN_CLAIM_WORDS = 4


def is_claim_bearing(sentence: str) -> bool:
    """Does this sentence assert something the documents could contradict?

    A grounding check on a sentence that makes no claim is a category error, and it was
    producing real false positives: "Let me search the documents again." scored
    P(ungrounded)=0.95, because none of its content words appear in the retrieved
    passage -- which is true, and means nothing. At Rs.75,000 stakes that was enough to
    BLOCK an agent's own progress narration.

    Deterministic and deliberately conservative: anything carrying a figure or a clause
    reference is claim-bearing regardless of how it opens, because "Let me confirm: the
    charge is 2%" is an assertion wearing a procedural hat.
    """
    stripped = sentence.strip()
    if not stripped:
        return False
    if _NUMBER.search(stripped) or _CLAUSE.search(stripped):
        return True
    if stripped.endswith("?"):
        # A question asserts nothing. The answer to it might.
        return False
    lowered = stripped.lower()
    if any(lowered.startswith(opener) for opener in _PROCEDURAL):
        return False
    return len(_WORD.findall(stripped)) >= _MIN_CLAIM_WORDS


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _context_text(context: Sequence[Fragment]) -> str:
    return "\n".join(fragment.text for fragment in context)


@dataclass(frozen=True, slots=True)
class GroundingScores:
    """The four raw scores, plus what produced them."""

    unsupported_content: float
    numeric_unsupported: float
    citation_unsupported: float
    overconfidence: float
    context_conflict: float = 0.0
    question_drift: float = 0.0
    #: False when the sentence asserts nothing checkable. Not a signal -- a gate on the
    #: others, recorded so a trace can show WHY everything came back at zero.
    claim_bearing: bool = True
    #: Figures and references that appear in the answer and nowhere in the context.
    unsupported_numbers: tuple[str, ...] = ()
    unsupported_citations: tuple[str, ...] = ()

    def as_readings(self, *, latency_ms: float = 0.0) -> list[SignalReading]:
        """Contract-1 ``SignalReading`` objects, with ``prob`` deliberately left None.

        These are raw scores. Only the calibrator may set ``prob``, and only ``prob``
        may enter expected-loss arithmetic (ADR-002). Populating it here would let an
        uncalibrated number reach the objective looking like a probability.
        """
        values = (
            self.unsupported_content,
            self.numeric_unsupported,
            self.citation_unsupported,
            self.overconfidence,
            self.context_conflict,
            self.question_drift,
        )
        return [
            SignalReading(name=name, raw=value, latency_ms=latency_ms)
            for name, value in zip(GROUNDING_SIGNALS, values, strict=True)
        ]

    def as_features(self) -> dict[str, float]:
        return {
            "grounding.unsupported_content": self.unsupported_content,
            "grounding.numeric_unsupported": self.numeric_unsupported,
            "grounding.citation_unsupported": self.citation_unsupported,
            "grounding.overconfidence": self.overconfidence,
            "grounding.context_conflict": self.context_conflict,
            "grounding.question_drift": self.question_drift,
        }


def unsupported_content(answer: str, context: Sequence[Fragment]) -> float:
    """Share of the answer's content words that appear nowhere in the context.

    The crudest possible grounding check and the one that catches a dropped retrieval:
    an answer written from the model's memory rather than from the passage in front of
    it will share far less vocabulary with that passage than one written from it.
    """
    answer_words = _words(answer)
    if not answer_words:
        return 0.0
    if not context:
        # Nothing was retrieved, so nothing is supported. Maximally suspicious, and
        # correct: an answer with no context behind it is ungrounded by definition.
        return 1.0
    supported = set(_words(_context_text(context)))
    missing = sum(1 for word in answer_words if word not in supported)
    return missing / len(answer_words)


def numeric_unsupported(answer: str, context: Sequence[Fragment]) -> tuple[float, tuple[str, ...]]:
    """Share of figures in the answer that do not appear in the context.

    The sharpest of the four. A rupee amount, a percentage or a number of days is either
    in the source or it is invented, and unlike prose there is no paraphrase defence.
    Returns the offending figures too -- L2 repair needs something to aim at.
    """
    figures = _NUMBER.findall(answer)
    if not figures:
        # No numeric claim is not the same as a supported one. Returning 0.0 says "this
        # signal found nothing to object to", which is what the fusion layer needs; the
        # absence of evidence is carried by the other three signals.
        return 0.0, ()
    haystack = _context_text(context)
    present = set(_NUMBER.findall(haystack))
    missing = tuple(
        figure
        for figure in figures
        if figure not in present and _normalise(figure) not in {_normalise(p) for p in present}
    )
    return len(missing) / len(figures), missing


def _normalise(figure: str) -> str:
    """``25,000`` and ``25000`` are the same figure; ``25.0`` and ``25`` are too."""
    cleaned = figure.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value == int(value) else str(value)


def citation_unsupported(answer: str, context: Sequence[Fragment]) -> tuple[float, tuple[str, ...]]:
    """Share of clause references in the answer absent from the context.

    Scene 1 in one number. The model cites Clause 7.4; the retrieved passage is 9.1.
    A citation to a clause nobody retrieved is the most checkable lie in the corpus.
    """
    cited = _CLAUSE.findall(answer)
    if not cited:
        return 0.0, ()
    present = set(_CLAUSE.findall(_context_text(context)))
    missing = tuple(reference for reference in cited if reference not in present)
    return len(missing) / len(cited), missing


def context_conflict(context: Sequence[Fragment]) -> float:
    """Do the retrieved passages disagree with each other?

    The only one of these signals that looks at the context rather than the answer, and
    the only one that can see a *contradiction*. When a superseded clause is retrieved
    alongside the current one, every answer-side check reports "supported" -- correctly,
    because the answer IS supported by a passage that happens to be the wrong one. No
    amount of comparing the answer to the context finds that; the disagreement is
    between two fragments, so that is where to look.

    Cheap and deterministic: two passages about the same subject quoting different
    figures. It does not decide which one is right -- that is the risk engine's job,
    with the corpus manifest's supersession data -- only that the question has two
    answers in front of it, which is enough to raise the stakes of getting it wrong.
    """
    by_domain: dict[str, list[set[str]]] = {}
    for fragment in context:
        figures = {_normalise(f) for f in _NUMBER.findall(fragment.text)}
        if figures:
            by_domain.setdefault(fragment.domain or "?", []).append(figures)

    conflicted = 0
    compared = 0
    for groups in by_domain.values():
        for i, left in enumerate(groups):
            for right in groups[i + 1 :]:
                compared += 1
                # Overlapping subject matter (they share at least one figure or are
                # both about the same domain) but disagreeing figures.
                if left != right and (left - right or right - left):
                    conflicted += 1
    if not compared:
        return 0.0
    return conflicted / compared


def question_drift(question: str, answer: str) -> float:
    """Does the answer address what was actually asked?

    The signal that sees an *unanswerable* question answered anyway. The context does
    settle something, and the answer is grounded in it -- just not in anything the
    customer asked about. Every grounding check passes; the answer is still useless and
    confidently so.

    Measured as the share of the question's content words the answer does not touch.
    Crude, and it will fire on a legitimately terse answer, which is why it is one
    signal among six rather than a rule.
    """
    question_words = set(_words(question))
    if not question_words:
        return 0.0
    answered = set(_words(answer))
    missed = sum(1 for word in question_words if word not in answered)
    return missed / len(question_words)


def hedge_density(answer: str) -> float:
    """How hedged the answer is, in [0, 1]."""
    words = _WORD.findall(answer.lower())
    if not words:
        return 0.0
    hits = sum(1 for word in words if word in _HEDGES)
    # Saturating: three hedges in a sentence is thoroughly hedged and a fourth adds
    # nothing. Without a ceiling this is a word-count signal wearing a hedge's name.
    return min(1.0, hits / 3.0)


def grounding_signals(
    answer: str, context: Sequence[Fragment], *, question: str = ""
) -> GroundingScores:
    """All six, over one sentence and the context it was supposed to come from."""
    if not is_claim_bearing(answer):
        # Nothing to be ungrounded about. Returning zeros is not the detector being
        # lenient; it is the detector declining to answer a question that was not asked.
        # The other lanes still see the sentence -- the canary rule, PII and the tool
        # interlock all run regardless, and none of them care whether a claim was made.
        return GroundingScores(
            unsupported_content=0.0,
            numeric_unsupported=0.0,
            citation_unsupported=0.0,
            overconfidence=1.0 - hedge_density(answer),
            context_conflict=context_conflict(context),
            question_drift=0.0,
            claim_bearing=False,
        )

    numeric, bad_numbers = numeric_unsupported(answer, context)
    citation, bad_citations = citation_unsupported(answer, context)
    return GroundingScores(
        unsupported_content=unsupported_content(answer, context),
        numeric_unsupported=numeric,
        citation_unsupported=citation,
        # Inverted: an unhedged assertion is the risky one. A wrong answer that says
        # "this may vary, please confirm" does much less damage than the same answer
        # stated flatly, because the reader is being told what to do with it.
        overconfidence=1.0 - hedge_density(answer),
        context_conflict=context_conflict(context),
        question_drift=question_drift(question, answer) if question else 0.0,
        unsupported_numbers=bad_numbers,
        unsupported_citations=bad_citations,
    )
