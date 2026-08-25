"""Canary tokens — the cheapest correct check in the system.

Plant a unique nonsense string in the system prompt **and** in the private corpus. If it
ever appears in an output there is no ambiguity and no judgement call: zero false
positives, instant hard stop, one string comparison.

Two details that published evaluations found the popular implementations get wrong, and
that this module therefore does deliberately:

* **Plant in both places.** Vigil and Rebuff plant only a prefix in the system prompt,
  and published evaluation found their default placements fail to detect real leaks. A
  canary in the retrieved corpus catches document exfiltration, which prompt-only
  placement cannot see.
* **Match on egress, not by prefixing.** We do not ask the model to echo anything. We
  scan what actually leaves, with Aho–Corasick: O(n) in the output length regardless of
  how many canaries are registered, so this stays free even at per-tenant scale.

A match is a deterministic **L5 block**. No model is in the loop, and no probability is
involved — invariant 6, and the point of ADR-008's hard-rule pre-pass.

Canary strings are secrets: they are minted at runtime, never committed, and never
logged in full (CLAUDE.md §9).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from interlock.core.types import SignalReading
from interlock.risk.objective import HardRule
from interlock.signals.base import Detector, DetectorOutcome, PreflightContext

__all__ = ["CANARY_PREFIX", "CanaryDetector", "CanaryRegistry", "redact"]

#: Recognisable in a trace, meaningless to a model, unlikely to occur naturally.
CANARY_PREFIX = "IL-CANARY"

try:  # pragma: no cover - exercised by whichever branch is installed
    import ahocorasick

    _HAVE_AHOCORASICK = True
except ImportError:  # pragma: no cover
    _HAVE_AHOCORASICK = False


def redact(canary: str) -> str:
    """A canary is a secret. Show enough to correlate, never enough to reproduce."""
    return f"{canary[:12]}...{canary[-4:]}" if len(canary) > 20 else f"{canary[:4]}..."


@dataclass
class CanaryRegistry:
    """Per-tenant canary strings, and an O(n) matcher over all of them.

    Per-tenant rather than global because a leak must identify *whose* data leaked, and
    because one tenant's canary appearing in another tenant's output is itself a finding.
    """

    _by_tenant: dict[str, list[str]] = field(default_factory=dict)
    _owner: dict[str, str] = field(default_factory=dict)
    _automaton: object | None = field(default=None, init=False, repr=False)
    _dirty: bool = field(default=True, init=False, repr=False)

    def mint(self, tenant_id: str) -> str:
        """Create and register a fresh canary for a tenant."""
        canary = f"{CANARY_PREFIX}-{tenant_id.upper()}-{secrets.token_hex(8).upper()}"
        self.register(tenant_id, canary)
        return canary

    def register(self, tenant_id: str, canary: str) -> None:
        self._by_tenant.setdefault(tenant_id, []).append(canary)
        self._owner[canary] = tenant_id
        self._dirty = True

    def canaries_for(self, tenant_id: str) -> list[str]:
        return list(self._by_tenant.get(tenant_id, []))

    def owner_of(self, canary: str) -> str | None:
        return self._owner.get(canary)

    @property
    def all_canaries(self) -> list[str]:
        return list(self._owner)

    def _rebuild(self) -> None:
        if not _HAVE_AHOCORASICK:
            self._automaton = None
            self._dirty = False
            return
        automaton = ahocorasick.Automaton()
        for canary in self._owner:
            automaton.add_word(canary, canary)
        if self._owner:
            automaton.make_automaton()
            self._automaton = automaton
        else:
            self._automaton = None
        self._dirty = False

    def scan(self, text: str) -> list[tuple[str, str, int]]:
        """Find every canary in ``text``.

        Returns ``(canary, tenant_id, end_offset)`` per match. Aho–Corasick when
        available so cost is O(len(text)) however many canaries are registered; the
        fallback is a plain scan, which is correct but linear in the canary count.
        """
        if not text or not self._owner:
            return []
        if self._dirty:
            self._rebuild()

        if self._automaton is not None:
            return [
                (canary, self._owner[canary], end)
                for end, canary in self._automaton.iter(text)  # type: ignore[attr-defined]
            ]
        return [
            (canary, tenant, text.index(canary))
            for canary, tenant in self._owner.items()
            if canary in text
        ]


@dataclass
class CanaryDetector:
    """Lane A half: confirm the tenant's canary is actually planted.

    The egress half — the part that fires — is ``scan_egress``, called by the gate on
    generated text. This half exists because a canary that was never planted cannot
    catch anything, and silently having no protection is worse than having none.
    """

    registry: CanaryRegistry
    name: str = "canary"

    async def scan(self, ctx: PreflightContext) -> DetectorOutcome:
        canaries = self.registry.canaries_for(ctx.tenant_id)
        if not canaries:
            return DetectorOutcome(
                signals=[SignalReading(name="canary_planted", raw=0.0, prob=None)],
                findings=["no canary registered for this tenant"],
            )

        haystack = f"{ctx.system_prompt}\n" + "\n".join(f.text for f in ctx.retrieved)
        planted = sum(1 for canary in canaries if canary in haystack)
        findings = []
        if planted == 0:
            findings.append("tenant canary is registered but not planted in this context")
        return DetectorOutcome(
            signals=[SignalReading(name="canary_planted", raw=float(planted), prob=None)],
            findings=findings,
        )

    def scan_egress(self, text: str, *, tenant_id: str | None = None) -> DetectorOutcome:
        """The half that fires. Called on generated text before it reaches the user.

        A match is an unconditional L5 block. Note it fires for *any* registered canary,
        not only the requesting tenant's: seeing another tenant's canary is a
        cross-tenant leak, which is strictly worse than leaking your own.
        """
        matches = self.registry.scan(text)
        if not matches:
            return DetectorOutcome(signals=[SignalReading(name="canary_leak", raw=0.0, prob=0.0)])

        canary, owner, _ = matches[0]
        cross_tenant = tenant_id is not None and owner != tenant_id
        reason = (
            f"canary token for tenant '{owner}' matched on egress"
            f"{' (CROSS-TENANT)' if cross_tenant else ''}"
        )
        return DetectorOutcome(
            # prob=1.0 is not a calibrated estimate; it is a certainty. The hard rule
            # below is what actually decides, and it never consults this number.
            signals=[SignalReading(name="canary_leak", raw=1.0, prob=1.0)],
            hard_rules=[HardRule(name="canary_leak", action="L5_block", reason=reason)],
            findings=[f"{reason}: {redact(canary)}"],
        )


def _assert_detector(detector: Detector) -> None:  # pragma: no cover - typing aid
    """Compile-time check that CanaryDetector satisfies the protocol."""
