"""L2 — regenerate the one bad sentence, with the real evidence injected.

The common case. One span is wrong and the rest of the answer is fine, so rewriting the
whole response would be both slower and worse. Mechanics from Implementation02 §4.3:

* Truncate the buffered sentence and re-prompt **the same model** — not a stronger one.
  Escalating the tier is L3, and it is priced separately; silently upgrading here would
  make the repair cost whatever a reroute costs while still being labelled a repair.
* Give it ``{context, question, answer_prefix, the specific unsupported claim, the
  retrieved evidence}``, with ``max_tokens≈80`` and ``stop=["\\n"]``. The constraints
  matter as much as the prompt: an unbounded repair can wander into a second paragraph,
  and then the gate is holding something that is no longer one sentence.
* **Verify the replacement through the same risk engine.** A repair nobody checked is
  just a different unverified sentence. Two failures escalate rather than loop.

Budget 150–400 ms, which is exactly what term ④ of the objective charges for. The cost
is billed to the ledger under ``component='repair'`` so verification spend stays visible
against the 5%-of-model-spend budget rather than hiding inside upstream cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from interlock.core.clock import monotonic_ms
from interlock.core.types import Decision, Fragment, RiskContext, Stakes

__all__ = ["RepairResult", "SentenceRepairer", "build_repair_messages"]

#: Open-weight models prepend a reasoning block even when told not to (finding F-004).
#: A non-streaming completion returns it inline, and taking "the first line" of such a
#: response yields the literal string "<think>" -- which was being shipped to customers
#: as the corrected sentence.
_REASONING_TAGS = "think|thinking|reasoning"
_REASONING_BLOCK = re.compile(
    rf"<({_REASONING_TAGS})\s*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
#: An unclosed opener: the completion was cut off part-way through its reasoning.
_UNCLOSED_BLOCK = re.compile(rf"<({_REASONING_TAGS})\s*>.*\Z", re.IGNORECASE | re.DOTALL)
_STRAY_TAG = re.compile(rf"</?({_REASONING_TAGS})\s*>", re.IGNORECASE)

#: Kept tight on purpose: one sentence, no preamble, no second paragraph.
DEFAULT_MAX_TOKENS = 80

#: Reasoning models spend tokens before writing a word of the answer. Without headroom
#: the whole budget goes on the block and the repair comes back empty.
REASONING_HEADROOM_TOKENS = 96

_SYSTEM = (
    "You correct a single sentence in a bank support answer. "
    "Rewrite ONLY the flagged sentence so that it is fully supported by the evidence "
    "provided. Cite the clause or document you relied on, in brackets. "
    "Reply with the corrected sentence and nothing else: no preamble, no explanation, "
    "no quotation marks. If the evidence does not settle the question, say plainly that "
    "the answer could not be confirmed from the customer's documents."
)


def build_repair_messages(
    *,
    sentence: str,
    question: str,
    answer_prefix: str,
    evidence: list[str],
    unsupported_claim: str = "",
) -> list[dict[str, str]]:
    """Assemble the repair prompt.

    ``answer_prefix`` is included so the replacement joins the sentences either side of
    it. A repair that is individually correct but reads as a non sequitur has produced a
    worse answer than the one it replaced.
    """
    evidence_block = (
        "\n".join(f"- {item.strip()}" for item in evidence if item.strip())
        or "- (no supporting passage was retrieved)"
    )
    claim = unsupported_claim.strip() or sentence.strip()

    user = (
        f"Customer question:\n{question.strip()}\n\n"
        f"Answer so far:\n{answer_prefix.strip() or '(this is the first sentence)'}\n\n"
        f"Flagged sentence:\n{sentence.strip()}\n\n"
        f"The unsupported claim:\n{claim}\n\n"
        f"Evidence actually retrieved:\n{evidence_block}\n\n"
        "Corrected sentence:"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


@dataclass(slots=True)
class RepairResult:
    """What one repair attempt produced, and what it cost."""

    text: str | None
    attempts: int = 0
    verified: bool = False
    latency_ms: float = 0.0
    tokens: int = 0
    reason: str = ""


@dataclass
class SentenceRepairer:
    """Re-prompts the same model for one sentence, then re-verifies the result."""

    provider: Any
    model: str
    risk_engine: Any
    stakes: Stakes
    request_id: str
    question: str = ""
    retrieved: list[Fragment] = field(default_factory=list)
    max_tokens: int = DEFAULT_MAX_TOKENS
    #: Extra budget for the reasoning block open-weight models emit regardless of
    #: instruction. Charged to the ledger like any other token, because it is spent.
    reasoning_headroom_tokens: int = REASONING_HEADROOM_TOKENS
    #: The plan specifies a newline stop sequence. VERIFIED AGAINST A LIVE MODEL
    #: AND REMOVED: on qwen3 that stop fires on the first newline INSIDE the
    #: <think> block, so the completion is the literal string "<think>" and every
    #: repair silently fails. One sentence is enforced by taking the first
    #: non-empty line after stripping reasoning instead.
    stop: list[str] | None = None
    max_attempts: int = 2
    verify_deadline_ms: float = 120.0
    #: Actions that mean the replacement is good enough to ship.
    _accepted: frozenset[str] = field(default_factory=lambda: frozenset({"L0_pass", "L1_annotate"}))
    #: Filled in for the ledger, so repair spend is visible against the 5% budget.
    last_result: RepairResult | None = field(default=None, init=False)

    async def __call__(self, sentence: str, decision: Decision, answer_prefix: str) -> str | None:
        """The callback shape ``CommitGate`` expects. Returns the replacement, or None."""
        result = await self.repair(sentence, decision, answer_prefix)
        self.last_result = result
        return result.text

    async def repair(self, sentence: str, decision: Decision, answer_prefix: str) -> RepairResult:
        started = monotonic_ms()
        hint = decision.repair_hint
        evidence = list(hint.evidence) if hint else self._fallback_evidence()
        claim = hint.unsupported_claim if hint else ""
        max_tokens = hint.suggested_max_tokens if hint else self.max_tokens

        attempts = 0
        for attempt in range(self.max_attempts):
            attempts = attempt + 1
            candidate = await self._generate(
                sentence=sentence,
                answer_prefix=answer_prefix,
                evidence=evidence,
                claim=claim,
                max_tokens=max_tokens,
            )
            if not candidate:
                continue

            verdict = await self._verify(candidate, answer_prefix)
            if verdict in self._accepted:
                return RepairResult(
                    text=candidate,
                    attempts=attempts,
                    verified=True,
                    latency_ms=monotonic_ms() - started,
                    tokens=(max_tokens + self.reasoning_headroom_tokens) * attempts,
                    reason=f"verified as {verdict}",
                )

        # Both attempts failed verification. Returning None escalates: a repair nobody
        # could verify is just a different unverified sentence, and a third attempt
        # costs more than rerouting.
        return RepairResult(
            text=None,
            attempts=attempts,
            verified=False,
            latency_ms=monotonic_ms() - started,
            tokens=(max_tokens + self.reasoning_headroom_tokens) * max(attempts, 1),
            reason="replacement failed verification twice",
        )

    # ------------------------------------------------------------------ #

    async def _generate(
        self,
        *,
        sentence: str,
        answer_prefix: str,
        evidence: list[str],
        claim: str,
        max_tokens: int,
    ) -> str | None:
        """One non-streaming completion. Failure returns None rather than raising."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": build_repair_messages(
                sentence=sentence,
                question=self.question,
                answer_prefix=answer_prefix,
                evidence=evidence,
                unsupported_claim=claim,
            ),
            "max_tokens": max_tokens + self.reasoning_headroom_tokens,
            "temperature": 0.0,  # deterministic, so a repair is replayable
        }
        if self.stop:
            body["stop"] = self.stop
        try:
            response = await self.provider.complete(body)
        except Exception:
            return None

        choices = response.get("choices") or []
        if not choices:
            return None
        text = str((choices[0].get("message") or {}).get("content") or "").strip()
        return self._tidy(text) or None

    @staticmethod
    def _tidy(text: str) -> str:
        """Strip the wrappers models add despite being told not to.

        Order matters twice over. Reasoning blocks come off first, because a completion
        that opens with ``<think>...</think>`` would otherwise have ``"<think>"`` taken
        as its first line and shipped to the customer as the corrected sentence -- which
        is exactly what happened the first time this ran against a live qwen3. Then the
        preamble comes off before the quotes, because a model that ignores both produces
        ``Corrected sentence: "..."`` and stripping quotes first finds none at the start,
        leaving one stranded mid-sentence.
        """
        text = _REASONING_BLOCK.sub(" ", text)
        # An opener with no closer means the completion was truncated part-way through
        # its reasoning; everything after it is reasoning, not an answer.
        text = _UNCLOSED_BLOCK.sub(" ", text)
        text = _STRAY_TAG.sub(" ", text).strip()

        # Then the first non-empty line. `stop` should have handled this, but not every
        # provider honours it, and a two-paragraph "sentence" breaks the gate's
        # one-sentence invariant.
        text = next((line.strip() for line in text.split("\n") if line.strip()), "")

        for prefix in ("Corrected sentence:", "Correction:", "Answer:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix) :].strip()
                break

        # Now the quotes, from whichever side they survive on.
        if len(text) >= 2 and text[0] in "\"'" and text[-1] in "\"'":
            text = text[1:-1]
        return text.strip().strip('"').strip()

    async def _verify(self, candidate: str, answer_prefix: str) -> str:
        """Re-price the replacement through the same engine that flagged the original."""
        ctx = RiskContext(
            request_id=self.request_id,
            sentence_idx=-1,  # a replacement, not a position in the original answer
            sentence=candidate,
            answer_prefix=answer_prefix,
            question=self.question,
            retrieved=list(self.retrieved),
            stakes=self.stakes,
            already_emitted=False,
            remaining_deadline_ms=self.verify_deadline_ms,
        )
        try:
            decision = await self.risk_engine.evaluate(ctx)
        except Exception:
            return "L4_hold"
        return str(decision.action)

    def _fallback_evidence(self) -> list[str]:
        """Without a verifier span, fall back to the retrieved passages themselves."""
        return [f.text for f in self.retrieved[:3] if f.text.strip()]
