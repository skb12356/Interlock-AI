"""The intervention ladder — L1 annotate, L3 reroute, L4 hold, L5 block.

Binary blocking is why guardrails get switched off in week two. The optimiser needs
cheap moves available, not just the nuclear one, and each rung here exists so the
expected-loss table has something affordable to choose.

**L1 is a deterministic string transform. No model is in the loop.** That is what makes
it cost ~0 ms and ₹0 of compute, which is in turn why the policy can price it at a
nuisance of ₹0.50 and the argmin will actually pick it on cheap traffic. The moment
annotation needs a generation, term ③ swallows the rung and the ladder collapses back
towards block-or-allow.

Two things annotation deliberately does **not** do:

* It does not rewrite the claim. Changing what a sentence asserts is L2's job, and it
  needs evidence and verification; quietly editing facts under the banner of
  "annotation" would be the most dangerous thing in this file.
* It does not soften a sentence into meaninglessness. The published finding is that
  *confidence is the instruction humans follow* — so the transform targets the
  confidence markers and appends the provenance, leaving the substance intact and
  visible for the reader to check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from interlock.core.types import Decision, Fragment

__all__ = ["Annotator", "HedgeMap", "build_citation"]

#: Overconfident phrasings and their hedged equivalents. Deliberately narrow: each
#: entry changes only how certain the sentence *sounds*, never what it claims.
HedgeMap = dict[str, str]

_DEFAULT_HEDGES: HedgeMap = {
    "will always": "should normally",
    "will never": "should not normally",
    "always": "normally",
    "never": "not normally",
    "definitely": "likely",
    "certainly": "likely",
    "guaranteed": "expected",
    "guarantees": "is expected to provide",
    "must be": "is generally",
    "is required to": "is generally expected to",
    "there is no": "there does not appear to be",
    "you are entitled to": "you may be entitled to",
    "you will receive": "you should receive",
    "this is": "this appears to be",
}

#: Applied to whole words only, so "always" inside "alwaysonline" is untouched.
_WORD = r"\b{}\b"

#: Appended when the sentence could not be grounded in what was retrieved. Phrased as a
#: statement about *our checking*, not about the claim: we know the claim is unverified,
#: we do not know that it is false.
_UNVERIFIED_NOTE = "[not verified against the retrieved documents]"


def build_citation(decision: Decision, fragments: list[Fragment]) -> str:
    """Build a citation from whatever evidence the decision carries.

    Prefers the specific evidence the verifier returned, because that is the passage
    the reader should actually check. Falls back to the retrieved document ids, which
    is weaker but still lets someone find the source.
    """
    hint = decision.repair_hint
    if hint and hint.evidence:
        first = hint.evidence[0].strip()
        clause = re.search(r"\b(Clause\s+\d+(?:\.\d+)*)", first, re.IGNORECASE)
        if clause:
            return f"({clause.group(1)})"

    doc_ids = [f.doc_id for f in fragments if f.doc_id]
    if doc_ids:
        unique = list(dict.fromkeys(doc_ids))[:2]
        return f"({', '.join(unique)})"
    return ""


@dataclass
class Annotator:
    """L1 — attach the citation, mark the unsupported clause, soften overconfidence.

    Pure, deterministic and instant. Given the same sentence and decision it returns the
    same string every time, which is what lets a decision be replayed bit-for-bit (F9).
    """

    hedges: HedgeMap = field(default_factory=lambda: dict(_DEFAULT_HEDGES))
    #: Above this calibrated P(ungrounded), append the unverified note.
    unverified_threshold: float = 0.30
    #: Above this calibrated P(overconfident), soften the confidence markers.
    hedge_threshold: float = 0.30

    def annotate(
        self,
        sentence: str,
        decision: Decision,
        fragments: list[Fragment] | None = None,
    ) -> str:
        """Return the annotated sentence. Never raises, never returns empty."""
        if not sentence.strip():
            return sentence

        result = sentence
        probs = decision.probs or {}

        if probs.get("overconfident", 0.0) >= self.hedge_threshold:
            result = self.soften(result)

        citation = build_citation(decision, fragments or [])
        if citation and citation not in result:
            result = self._append(result, citation)

        ungrounded = max(
            probs.get("ungrounded", 0.0),
            probs.get("contradicted", 0.0),
        )
        if ungrounded >= self.unverified_threshold and _UNVERIFIED_NOTE not in result:
            result = self._append(result, _UNVERIFIED_NOTE)

        return result

    def soften(self, sentence: str) -> str:
        """Lower the confidence a sentence projects, without changing what it claims.

        Longest phrases first, so "will always" is matched before "always" and the
        result reads as English rather than as "will normally normally".
        """
        result = sentence
        for phrase in sorted(self.hedges, key=len, reverse=True):
            replacement = self.hedges[phrase]
            result = re.sub(
                _WORD.format(re.escape(phrase)),
                replacement,
                result,
                flags=re.IGNORECASE,
            )
        return result

    @staticmethod
    def _append(sentence: str, suffix: str) -> str:
        """Insert before the terminating punctuation, so the sentence still reads well.

        "No charge applies." + "(Clause 9.1)" becomes "No charge applies (Clause 9.1)."
        rather than "No charge applies. (Clause 9.1)".
        """
        stripped = sentence.rstrip()
        trailing = sentence[len(stripped) :]
        match = re.search(r"([.!?]+[\"')\]]*)$", stripped)
        if match:
            body = stripped[: match.start()]
            return f"{body} {suffix}{match.group(1)}{trailing}"
        return f"{stripped} {suffix}{trailing}"
