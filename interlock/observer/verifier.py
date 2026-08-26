"""Claim-level grounding: which *part* of this sentence is unsupported?

The probe answers *is this sentence grounded?* with a number. That is enough to decide
whether to act, and not enough to act well. L2 repair needs to know **what to fix** — and
`TODO.md` says it plainly: *without the span, L2 repair has nothing to aim at.*

So this splits a sentence into atomic claims and checks each one against the retrieved
context independently. A sentence like

    "The annual fee is Rs. 500 and it is waived above Rs. 2 lakh of spend."

carries two claims. If the fee is right and the waiver is invented, repairing the whole
sentence risks losing the correct half; repairing the second clause does not.

**MiniCheck-class, not a generative judge.** An NLI cross-encoder scores
``(context, claim)`` pairs in a single forward pass. CLAUDE.md §3 forbids a generative
judge on this path, and this has no ``generate`` call — the same discipline as the probe.

**Splitting is deterministic.** Coordinated clauses, semicolons and appositive relative
clauses, by rule. A model-based claim splitter would be a second thing to calibrate, a
second thing to monitor for drift, and a second thing to blame when a span is wrong.

**A claim the verifier cannot judge is reported as unjudged, never as supported.** The
same rule as the deep judge: collapsing "I could not tell" into "it is fine" makes the
verifier systematically agree with whatever shipped.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from interlock.core.types import Fragment

__all__ = ["ClaimVerdict", "ClaimVerifier", "SentenceVerdict", "split_claims"]

#: An NLI cross-encoder with its classification head intact -- unlike the probe encoder,
#: which drops the head and reads hidden states instead. Both are the same family; they
#: are asking different questions of it.
DEFAULT_VERIFIER = "cross-encoder/nli-distilroberta-base"

#: Below this, a fragment is not a claim -- it is a connective or a stray clause, and
#: checking it against the context produces noise with a span attached.
MIN_CLAIM_CHARS = 25

#: Splits on coordination and punctuation that genuinely separate assertions. Deliberately
#: conservative: an over-eager splitter produces fragments that entail nothing, and every
#: one of those becomes a false "unsupported claim" with a span pointing at half a thought.
_SPLIT = re.compile(
    r"""
    \s*;\s*                                  # semicolons always separate
    | \s+and\s+(?=(?:the|it|this|that|there|a|an|you|we|they|[A-Z0-9]))
    | \s+but\s+(?=(?:the|it|this|that|there|a|an|you|we|they|[A-Z0-9]))
    | \s+(?:however|whereas|although)\s+
    """,
    re.VERBOSE | re.IGNORECASE,
)


def split_claims(sentence: str, *, min_chars: int = MIN_CLAIM_CHARS) -> list[tuple[int, int]]:
    """Character spans of the atomic claims in ``sentence``.

    Returns spans rather than strings so a caller can point at the original text. A
    repair that is handed a *rewritten* claim has to find it again in the sentence, and
    that search fails on exactly the sentences worth repairing.
    """
    stripped = sentence.strip()
    if not stripped:
        return []

    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SPLIT.finditer(sentence):
        if match.start() - cursor >= min_chars:
            spans.append((cursor, match.start()))
            cursor = match.end()
    if len(sentence) - cursor >= min_chars:
        spans.append((cursor, len(sentence)))

    # One claim that is the whole sentence is the normal case, not a failure.
    return spans or [(0, len(sentence))]


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """One claim, its span, and what the verifier made of it."""

    span: tuple[int, int]
    text: str
    #: 'supported' | 'contradicted' | 'unjudged'
    label: str
    #: Entailment probability. None when the claim was not judged.
    support: float | None = None
    #: The passage that best supported or contradicted it, for the review card.
    evidence: str = ""

    @property
    def unsupported(self) -> bool:
        return self.label == "contradicted"


@dataclass(frozen=True, slots=True)
class SentenceVerdict:
    """Every claim in one sentence, and the span a repair should aim at."""

    sentence: str
    claims: list[ClaimVerdict] = field(default_factory=list)

    @property
    def worst(self) -> ClaimVerdict | None:
        """The least-supported judged claim. What L2 repair targets."""
        judged = [c for c in self.claims if c.support is not None]
        return min(judged, key=lambda c: c.support or 1.0) if judged else None

    @property
    def offending_span(self) -> tuple[int, int] | None:
        worst = self.worst
        return worst.span if worst and worst.unsupported else None

    @property
    def any_unsupported(self) -> bool:
        return any(claim.unsupported for claim in self.claims)

    @property
    def unjudged(self) -> int:
        return sum(1 for claim in self.claims if claim.label == "unjudged")

    def as_dict(self) -> dict[str, Any]:
        return {
            "sentence": self.sentence,
            "any_unsupported": self.any_unsupported,
            "offending_span": list(self.offending_span) if self.offending_span else None,
            "unjudged_claims": self.unjudged,
            "claims": [
                {
                    "span": list(claim.span),
                    "text": claim.text,
                    "label": claim.label,
                    "support": claim.support,
                    "evidence": claim.evidence[:200],
                }
                for claim in self.claims
            ],
        }


@dataclass
class ClaimVerifier:
    """Scores each claim against each retrieved passage with an NLI cross-encoder."""

    model_name: str = DEFAULT_VERIFIER
    max_tokens: int = 384
    #: Entailment probability below which a claim counts as unsupported. Not a tuned
    #: number -- it feeds the calibrator like everything else, and the calibrated value
    #: is what the objective prices.
    support_threshold: float = 0.5
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _model: Any = field(default=None, init=False, repr=False)
    _entail_index: int = field(default=-1, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            model.eval()
            torch.set_grad_enabled(False)

            # Find the entailment class by NAME rather than by index. Label order differs
            # between NLI checkpoints -- some are (contradiction, neutral, entailment),
            # others are not -- and hardcoding an index gives a verifier that is
            # confidently backwards on half the models it might be pointed at.
            labels = {v.lower(): k for k, v in getattr(model.config, "id2label", {}).items()}
            self._entail_index = labels.get("entailment", labels.get("label_2", -1))

            self._tokenizer = tokenizer
            self._model = model

    @property
    def available(self) -> bool:
        return self._model is not None and self._entail_index >= 0

    # ------------------------------------------------------------------ #

    def verify(self, sentence: str, context: Sequence[Fragment]) -> SentenceVerdict:
        """Check every claim in ``sentence`` against the trusted retrieved passages."""
        spans = split_claims(sentence)
        passages = [
            fragment.text
            for fragment in context
            if not str(fragment.provenance).endswith("untrusted") and fragment.text.strip()
        ]
        if not passages:
            # Nothing trusted to check against. Every claim is unjudged, which is a
            # different report from every claim being unsupported.
            return SentenceVerdict(
                sentence=sentence,
                claims=[
                    ClaimVerdict(
                        span=span, text=sentence[span[0] : span[1]].strip(), label="unjudged"
                    )
                    for span in spans
                ],
            )

        try:
            self.load()
        except Exception:
            return SentenceVerdict(
                sentence=sentence,
                claims=[
                    ClaimVerdict(
                        span=span, text=sentence[span[0] : span[1]].strip(), label="unjudged"
                    )
                    for span in spans
                ],
            )

        claims: list[ClaimVerdict] = []
        for span in spans:
            text = sentence[span[0] : span[1]].strip()
            support, evidence = self._best_support(text, passages)
            if support is None:
                claims.append(ClaimVerdict(span=span, text=text, label="unjudged"))
                continue
            # A claim is supported if ANY passage entails it. Requiring every passage to
            # entail it would mark a correct claim unsupported the moment retrieval
            # returned a second, unrelated document -- which it always does.
            label = "supported" if support >= self.support_threshold else "contradicted"
            claims.append(
                ClaimVerdict(span=span, text=text, label=label, support=support, evidence=evidence)
            )
        return SentenceVerdict(sentence=sentence, claims=claims)

    def _best_support(self, claim: str, passages: Sequence[str]) -> tuple[float | None, str]:
        import torch

        try:
            with self._lock, torch.inference_mode():
                encoded = self._tokenizer(
                    list(passages),
                    [claim] * len(passages),
                    return_tensors="pt",
                    truncation=True,
                    max_length=self.max_tokens,
                    padding=True,
                )
                logits = self._model(**encoded).logits
                probabilities = torch.softmax(logits, dim=-1)[:, self._entail_index]
            best = int(torch.argmax(probabilities).item())
            return float(probabilities[best].item()), passages[best]
        except Exception:
            return None, ""

    def health(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "loaded": self._model is not None,
            "entailment_index": self._entail_index,
            "support_threshold": self.support_threshold,
            "generative": False,
        }
