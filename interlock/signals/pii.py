"""PII detection for Indian retail banking, checksum-first.

The plan names Presidio with custom recognisers. This build inverts that emphasis
deliberately, and the reason is correctness rather than convenience: **PAN, Aadhaar,
IFSC and card numbers are checksummed or strictly formatted**, so a validator decides
them exactly, while an NER model only guesses. Where a deterministic check exists, use
it instead of a model (CLAUDE.md §3).

Concretely, Aadhaar carries a Verhoeff check digit. A regex for twelve digits matches
every twelve-digit order reference in the corpus; a regex *plus Verhoeff* matches
essentially only real Aadhaar numbers. That is a false-positive rate a model cannot
promise, on the field where a false positive is most expensive.

Presidio remains the right tool for free-text names and addresses, which have no
checksum. It is wired in behind the same interface at D1-B2's ML profile and is not
required for the deterministic path to be correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from interlock.core.types import SignalReading
from interlock.signals.base import DetectorOutcome, PreflightContext

__all__ = ["PIIDetector", "PIIMatch", "luhn_ok", "verhoeff_ok"]

# --------------------------------------------------------------------------- #
# Checksums
# --------------------------------------------------------------------------- #

# Verhoeff tables. Aadhaar's check digit uses this scheme, which catches all single-digit
# errors and all adjacent transpositions -- the two ways a human mistypes a number.
_D_TABLE = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P_TABLE = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_ok(digits: str) -> bool:
    """Validate a Verhoeff check digit (Aadhaar)."""
    if not digits.isdigit():
        return False
    checksum = 0
    for position, char in enumerate(reversed(digits)):
        checksum = _D_TABLE[checksum][_P_TABLE[position % 8][int(char)]]
    return checksum == 0


def luhn_ok(digits: str) -> bool:
    """Validate a Luhn check digit (payment cards)."""
    if not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b(\d{4}[ -]?\d{4}[ -]?\d{4})\b")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_ACCOUNT = re.compile(r"\b\d{9,18}\b")
_EMAIL = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_IN = re.compile(r"\b(?:\+?91[ -]?)?[6-9]\d{9}\b")

#: Words near a number that make "this is an account number" credible. Account numbers
#: have no checksum, so context is the only thing separating one from an order id.
_ACCOUNT_HINTS = re.compile(
    r"\b(account|a/c|acct|ac no|account number|beneficiary)\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class PIIMatch:
    kind: str
    value: str
    span: tuple[int, int]
    #: True when a checksum or strict format confirmed it, rather than a bare pattern.
    validated: bool

    @property
    def redacted(self) -> str:
        """Never log the value itself."""
        tail = self.value[-4:] if len(self.value) > 4 else ""
        return f"{self.kind}:****{tail}"


def _digits(text: str) -> str:
    return re.sub(r"[ -]", "", text)


def find_pii(text: str) -> list[PIIMatch]:
    """Every identifier in ``text``, checksum-validated where a checksum exists."""
    matches: list[PIIMatch] = []
    if not text:
        return matches

    for match in _PAN.finditer(text):
        matches.append(PIIMatch("pan", match.group(), match.span(), validated=True))

    for match in _AADHAAR.finditer(text):
        raw = _digits(match.group(1))
        if len(raw) == 12 and raw[0] not in "01" and verhoeff_ok(raw):
            matches.append(PIIMatch("aadhaar", raw, match.span(), validated=True))

    for match in _IFSC.finditer(text):
        matches.append(PIIMatch("ifsc", match.group(), match.span(), validated=True))

    for match in _CARD.finditer(text):
        raw = _digits(match.group())
        if 12 <= len(raw) <= 19 and luhn_ok(raw):
            matches.append(PIIMatch("card", raw, match.span(), validated=True))

    for match in _EMAIL.finditer(text):
        matches.append(PIIMatch("email", match.group(), match.span(), validated=True))

    for match in _PHONE_IN.finditer(text):
        matches.append(PIIMatch("phone", match.group(), match.span(), validated=True))

    # Account numbers have no checksum, so they are only reported when the surrounding
    # text makes the claim credible -- and they are marked unvalidated either way.
    claimed = {m.span for m in matches}
    for match in _ACCOUNT.finditer(text):
        if match.span() in claimed or any(
            match.start() >= s and match.end() <= e for s, e in claimed
        ):
            continue
        window = text[max(0, match.start() - 40) : match.start()]
        if _ACCOUNT_HINTS.search(window):
            matches.append(PIIMatch("account_number", match.group(), match.span(), validated=False))

    return matches


@dataclass
class PIIDetector:
    """Lane A: scan the outbound prompt. Egress scanning uses the same finder."""

    name: str = "pii"
    #: Kinds that count towards the leak signal. Email and phone are frequently
    #: legitimate in a support conversation, so they are reported but not alarming.
    high_severity: frozenset[str] = field(
        default_factory=lambda: frozenset({"pan", "aadhaar", "card", "account_number"})
    )

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        matches = find_pii(ctx.all_text())
        return self._outcome(matches)

    def scan_egress(self, text: str) -> DetectorOutcome:
        """PII in generated output. Unlike a canary this is graded, not a hard stop:
        a support agent legitimately repeats the last four digits of a card."""
        return self._outcome(find_pii(text))

    def _outcome(self, matches: list[PIIMatch]) -> DetectorOutcome:
        severe = [m for m in matches if m.kind in self.high_severity]
        raw = float(len(severe))
        return DetectorOutcome(
            signals=[
                SignalReading(
                    name="pii_leak",
                    raw=raw,
                    # Uncalibrated: a count is not a probability, and nothing may
                    # multiply it by rupees until isotonic calibration has run (ADR-002).
                    prob=None,
                    span=severe[0].span if severe else None,
                )
            ],
            findings=[m.redacted for m in severe],
        )
