"""Prompt-injection detection.

**The structural point, which matters more than the classifier.** This runs on the last
user turn *and on every retrieved chunk separately*. A poisoned paragraph buried in 50 KB
of legitimate context barely moves a whole-prompt score — averaging is precisely how the
poisoned-PDF case gets missed. Scanning per chunk also gives the tool interlock what it
needs later: not "this request looks risky" but *which fragment* is untrusted.

Two backends behind one interface:

* ``PatternInjectionBackend`` (default) — deterministic rules over the documented
  injection grammar: instruction override, exfiltration imperatives, hidden text, role
  reassignment. Cheap, explainable, no weights to download.
* ``TransformerInjectionBackend`` — `protectai/deberta-v3-base-prompt-injection-v2`,
  lazily imported so the module is importable without torch installed. Enable with
  ``make install-ml``.

The pattern backend is not a placeholder for the model: it catches the attacks that
carry an unambiguous signature (which includes the demo's) with no false positives on
the corpus, and its findings are readable in the console. The model backend is what
generalises to paraphrase. They compose — the maximum of the two is used.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

from interlock.core.types import Fragment, SignalReading
from interlock.signals.base import DetectorOutcome, PreflightContext

__all__ = [
    "InjectionBackend",
    "InjectionDetector",
    "PatternInjectionBackend",
    "TransformerInjectionBackend",
    "strip_hidden_text",
]

# --------------------------------------------------------------------------- #
# The injection grammar
# --------------------------------------------------------------------------- #

_RULES: list[tuple[str, re.Pattern[str], float]] = [
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(previous|prior|earlier|above|all)\b[^.\n]{0,20}"
            r"\b(instruction|prompt|direction|rule|message)s?\b",
            re.IGNORECASE,
        ),
        0.95,
    ),
    (
        "role_reassignment",
        re.compile(
            r"\byou\s+are\s+now\b|\bact\s+as\s+(?:a|an|the)\b|\bnew\s+(?:system\s+)?"
            r"(?:prompt|instructions?|persona)\b|\bpretend\s+(?:to\s+be|you)\b",
            re.IGNORECASE,
        ),
        0.75,
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(email|send|forward|upload|post|transmit|share)\b[^.\n]{0,60}"
            r"(?:to\s+)?[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}",
            re.IGNORECASE,
        ),
        0.9,
    ),
    (
        "exfiltration_url",
        re.compile(
            r"\b(send|post|upload|exfiltrate|transmit)\b[^.\n]{0,60}https?://",
            re.IGNORECASE,
        ),
        0.85,
    ),
    (
        "authority_claim",
        re.compile(
            r"\b(?:this\s+has\s+)?already\s+been\s+(?:approved|authorised|authorized|verified)\b"
            r"|\bthe\s+(?:audit|security|compliance)\s+team\s+(?:has|have)\b",
            re.IGNORECASE,
        ),
        0.6,
    ),
    (
        "secret_disclosure",
        re.compile(
            r"\b(reveal|repeat|print|show|output|disclose)\b[^.\n]{0,30}"
            r"\b(system\s+prompt|instructions|api\s+key|password|secret)\b",
            re.IGNORECASE,
        ),
        0.9,
    ),
    (
        "compliance_bait",
        re.compile(
            r"\bconfirm\s+that\s+you\s+have\s+done\s+so\b|\bdo\s+not\s+mention\b"
            r"|\bwithout\s+(?:telling|informing|notifying)\s+the\s+user\b",
            re.IGNORECASE,
        ),
        0.7,
    ),
]

#: Text hidden from a human reader but perfectly visible to a model. This is the
#: mechanism of the demo's poisoned PDF, and it is a strong signal on its own: legitimate
#: documents do not paint their instructions white.
_HIDDEN_TEXT = [
    (
        "white_text_css",
        re.compile(
            r"color\s*:\s*(?:#f{3,6}|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))", re.IGNORECASE
        ),
        0.8,
    ),
    ("zero_size_font", re.compile(r"font-size\s*:\s*0(?:\.0+)?(?:px|pt|em)?", re.IGNORECASE), 0.8),
    ("display_none", re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE), 0.8),
]

#: Zero-width and bidirectional control characters, used to smuggle instructions past
#: a human reviewer entirely.
_INVISIBLE = {
    "​",
    "‌",
    "‍",
    "⁠",
    "﻿",
    "‪",
    "‫",
    "‬",
    "‭",
    "‮",
}


def strip_hidden_text(text: str) -> tuple[str, list[str]]:
    """Remove invisible characters, reporting what was removed.

    Returned separately rather than silently cleaned: the fact that a document contained
    bidirectional overrides is itself the finding.
    """
    found = sorted({unicodedata.name(ch, repr(ch)) for ch in text if ch in _INVISIBLE})
    if not found:
        return text, []
    return "".join(ch for ch in text if ch not in _INVISIBLE), found


class InjectionBackend(Protocol):
    name: str

    def score(self, text: str) -> tuple[float, list[str]]:
        """Return (score in [0,1], human-readable reasons)."""
        ...


@dataclass
class PatternInjectionBackend:
    """Deterministic rules over the documented injection grammar."""

    name: str = "pattern"

    def score(self, text: str) -> tuple[float, list[str]]:
        if not text:
            return 0.0, []
        cleaned, invisible = strip_hidden_text(text)

        best = 0.0
        reasons: list[str] = []
        for label, pattern, weight in _RULES:
            if pattern.search(cleaned):
                reasons.append(label)
                best = max(best, weight)
        for label, pattern, weight in _HIDDEN_TEXT:
            if pattern.search(text):
                reasons.append(label)
                best = max(best, weight)
        if invisible:
            reasons.append(f"invisible_characters({len(invisible)})")
            best = max(best, 0.7)

        # Several independent signatures at once is much stronger than any one of them.
        if len(reasons) >= 2:
            best = min(1.0, best + 0.05 * (len(reasons) - 1))
        return best, reasons


@dataclass
class TransformerInjectionBackend:
    """`protectai/deberta-v3-base-prompt-injection-v2`, imported lazily.

    Lazy so that `interlock.signals.injection` imports on a machine with no torch. If
    the weights are unavailable the backend reports itself unavailable and Lane A falls
    back to patterns rather than failing the request.
    """

    model_id: str = "protectai/deberta-v3-base-prompt-injection-v2"
    name: str = "transformer"
    _pipeline: object | None = field(default=None, init=False, repr=False)
    _unavailable: str | None = field(default=None, init=False, repr=False)

    @property
    def available(self) -> bool:
        if self._pipeline is not None:
            return True
        if self._unavailable is not None:
            return False
        try:  # pragma: no cover - depends on the ml extra being installed
            from transformers import pipeline

            self._pipeline = pipeline("text-classification", model=self.model_id, truncation=True)
            return True
        except Exception as exc:
            self._unavailable = repr(exc)
            return False

    def score(self, text: str) -> tuple[float, list[str]]:
        if not text or not self.available:
            return 0.0, []
        try:  # pragma: no cover - depends on the ml extra
            result = self._pipeline(text[:2000])  # type: ignore[operator]
            row = result[0] if isinstance(result, list) else result
            label = str(row.get("label", "")).upper()
            confidence = float(row.get("score", 0.0))
            score = confidence if label == "INJECTION" else 1.0 - confidence
            return score, [f"transformer:{label.lower()}:{confidence:.2f}"]
        except Exception as exc:
            self._unavailable = repr(exc)
            return 0.0, []


@dataclass
class InjectionDetector:
    """Scans the last user turn and every retrieved chunk, separately."""

    backend: InjectionBackend = field(default_factory=PatternInjectionBackend)
    name: str = "injection"
    #: Above this, a retrieved fragment is re-labelled untrusted, which is what the
    #: tool interlock later joins over. Deliberately not a blocking threshold: the
    #: expected-loss table decides what to *do*, this only decides what to *believe*.
    untrusted_threshold: float = 0.6

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        signals: list[SignalReading] = []
        findings: list[str] = []

        user_score, user_reasons = self.backend.score(ctx.last_user_message)
        signals.append(SignalReading(name="injection_user", raw=user_score, prob=None))
        if user_reasons:
            findings.append(f"user turn: {', '.join(user_reasons)}")

        worst = user_score
        for index, fragment in enumerate(ctx.retrieved):
            score, reasons = self.backend.score(fragment.text)
            if score <= 0.0:
                continue
            label = fragment.doc_id or f"chunk{index}"
            signals.append(SignalReading(name=f"injection_chunk:{label}", raw=score, prob=None))
            if reasons:
                findings.append(f"{label}: {', '.join(reasons)}")
            worst = max(worst, score)

        signals.append(SignalReading(name="injection", raw=worst, prob=None))
        return DetectorOutcome(signals=signals, findings=findings)

    def untrusted_fragments(self, fragments: list[Fragment]) -> list[Fragment]:
        """Re-label fragments that look injected.

        Returns new objects rather than mutating: the original labels are evidence, and
        the console shows both what a fragment claimed to be and what we decided it was.
        """
        out: list[Fragment] = []
        for fragment in fragments:
            score, _ = self.backend.score(fragment.text)
            if score >= self.untrusted_threshold and fragment.provenance != "system":
                out.append(fragment.model_copy(update={"provenance": "retrieved_untrusted"}))
            else:
                out.append(fragment)
        return out
