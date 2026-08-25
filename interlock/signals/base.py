"""The detector interface Lane A orchestrates.

Every pre-flight detector implements ``Detector``. Two properties of the shape matter:

* A detector returns **both** graded signals and, where it has one, a **deterministic
  hard rule**. Where a provably-correct check exists, use it instead of a model
  (CLAUDE.md §3): a canary match is a string comparison, not a probability judgement,
  and it must be able to say so rather than emitting a score for the optimiser to weigh.
* A detector that fails is **not** an error. Lane A drops it and records its absence as
  a signal with ``prob=None``, because a missing signal is information — "we did not
  check" is different from "we checked and found nothing", and the console shows which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from interlock.core.types import Fragment, SignalReading
from interlock.risk.objective import HardRule

__all__ = ["Detector", "DetectorOutcome", "PreflightContext"]


@dataclass(slots=True)
class PreflightContext:
    """Everything a pre-flight detector may look at.

    Retrieved fragments are carried **separately** from the flattened prompt because the
    injection detector must scan every chunk on its own. A poisoned chunk buried in
    50 KB of legitimate context does not move a whole-prompt classifier's score, which
    is exactly how the poisoned-PDF case gets missed.
    """

    request_id: str
    tenant_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[Fragment] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    user_role: str = "customer"

    @property
    def last_user_message(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""

    @property
    def system_prompt(self) -> str:
        return "\n".join(
            str(m.get("content") or "") for m in self.messages if m.get("role") == "system"
        )

    @property
    def conversation_depth(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    def all_text(self) -> str:
        """The flattened prompt, for detectors that genuinely want the whole thing."""
        parts = [str(m.get("content") or "") for m in self.messages]
        parts.extend(fragment.text for fragment in self.retrieved)
        return "\n".join(parts)


@dataclass(slots=True)
class DetectorOutcome:
    """What one detector found."""

    signals: list[SignalReading] = field(default_factory=list)
    #: Deterministic rules that fired. These short-circuit the optimiser (ADR-008).
    hard_rules: list[HardRule] = field(default_factory=list)
    #: Human-readable, folded into the stakes rationale and shown in the console.
    findings: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> DetectorOutcome:
        return cls()


class Detector(Protocol):
    """A pre-flight detector. Must be cheap, and must not raise."""

    name: str

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        """Inspect the request. Returning an empty outcome means 'nothing found'."""
        ...
