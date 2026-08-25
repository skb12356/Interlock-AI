"""What does this action *do*, and can it be taken back?

Invariant 5: tool calls are gated on action semantics, not words. A call named
``send_email`` is not dangerous because of the word "email" -- it is dangerous because
once it leaves, it is gone. So the axis is reversibility, declared per tool in the
policy file where risk and compliance can read it, never inferred from the name.

Crossed with the provenance of whatever motivated the call, that gives the matrix::

                    | reversible | costly     | irreversible
    system          | allow      | allow      | allow
    user            | allow      | allow      | allow
    retrieved_verified | allow   | allow      | allow
    retrieved_untrusted| allow   | HOLD       | HOLD
    tool_external      | allow   | HOLD       | HOLD

Two properties of that table are load-bearing, and both are about *not* over-blocking:

* **The reversible column never holds.** A balance lookup triggered by a poisoned
  document is not an incident -- it reads data the customer already owns, and nothing
  about it is hard to undo. Freezing it would train operators to approve everything,
  which is how a review queue stops being a control.
* **The user row allows irreversible actions.** A customer asking to transfer their own
  money is the product working. Untrusted *content* is the threat; the user is not.

The monetary cap is a separate axis and it is not a matrix cell. ``max_auto_inr`` holds
a call whose amount exceeds it **whatever its provenance** -- a large transfer the user
genuinely asked for still deserves a human, and that is a policy question rather than a
security one. Keeping it out of the matrix keeps the matrix about taint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from interlock.core.policy import Policy
from interlock.core.types import PROVENANCE_ORDER, Provenance, Reversibility
from interlock.interlock_tools.provenance import TaintVerdict, ToolCall, argument_strings

__all__ = [
    "REVERSIBILITY_ORDER",
    "ToolDecision",
    "evaluate_tool_call",
    "largest_amount",
    "resolve_reversibility",
]

#: Least to most consequential. Used for the "at least this bad" comparisons below.
REVERSIBILITY_ORDER: tuple[Reversibility, ...] = ("reversible", "costly", "irreversible")

#: The taint at which a non-reversible tool freezes. Everything at or above
#: ``retrieved_untrusted`` in the lattice is content an attacker may have written.
_UNTRUSTED_FLOOR = PROVENANCE_ORDER.index("retrieved_untrusted")

#: Argument names that carry a monetary value. Checked before falling back to scanning
#: every argument, so that ``{"reference": 50000}`` on a lookup is not read as an amount.
_AMOUNT_KEYS = ("amount", "amount_inr", "value", "sum", "total", "transfer_amount")


@dataclass(frozen=True, slots=True)
class ToolDecision:
    """Allow or freeze, with the whole reason -- the console explains, never asks."""

    allowed: bool
    tool: str
    reversibility: Reversibility
    taint: Provenance
    #: Which tier of provenance attribution produced ``taint`` (see ADR-007).
    tier: int
    reason: str
    #: Populated when a monetary cap fired rather than the taint matrix.
    amount_inr: float | None = None
    cap_inr: float | None = None
    #: doc_ids a reviewer should look at first.
    evidence_doc_ids: tuple[str, ...] = ()

    @property
    def held(self) -> bool:
        return not self.allowed


def resolve_reversibility(tool_name: str, policy: Policy) -> tuple[Reversibility, bool]:
    """Look the tool up in the policy. Returns ``(reversibility, was_declared)``.

    An undeclared tool falls back to the policy's ``default`` entry, which the schema
    requires to exist. ``was_declared`` is returned rather than swallowed because
    "we froze a tool nobody has classified" is a different operational message from
    "we froze a tool classified as irreversible", and an operator needs to be told to
    go and classify it.
    """
    return policy.tool(tool_name).reversibility, tool_name in policy.tools


def largest_amount(call: ToolCall) -> float | None:
    """The monetary value this call moves, if it declares one.

    Named keys first. Only if none are present does this fall back to scanning, and
    even then it takes the largest *numeric* argument -- a reference number is not an
    amount, and treating one as an amount would hold half the reversible traffic.
    """
    for key in _AMOUNT_KEYS:
        if key in call.arguments:
            parsed = _as_amount(call.arguments[key])
            if parsed is not None:
                return parsed
    return None


def _as_amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("₹", "").replace("Rs.", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def evaluate_tool_call(
    call: ToolCall,
    verdict: TaintVerdict,
    policy: Policy,
) -> ToolDecision:
    """The interlock itself. Deterministic, no model in the loop (CLAUDE.md s3)."""
    reversibility, declared = resolve_reversibility(call.name, policy)
    tool_policy = policy.tool(call.name)
    taint_rank = PROVENANCE_ORDER.index(verdict.taint)

    undeclared_note = (
        ""
        if declared
        else f" (tool '{call.name}' is not declared in the policy; defaulted to {reversibility})"
    )

    # -- axis 1: the monetary cap, whatever the provenance ---------------------
    cap = tool_policy.max_auto_inr
    amount = largest_amount(call)
    if cap is not None and amount is not None and amount > cap:
        return ToolDecision(
            allowed=False,
            tool=call.name,
            reversibility=reversibility,
            taint=verdict.taint,
            tier=verdict.tier,
            amount_inr=amount,
            cap_inr=cap,
            evidence_doc_ids=verdict.matched_doc_ids,
            reason=(
                f"{call.name} moves Rs.{amount:,.2f}, above the policy's automatic "
                f"ceiling of Rs.{cap:,.2f}; a human must approve it"
            ),
        )

    # -- axis 2: the taint x reversibility matrix ------------------------------
    if reversibility == "reversible":
        return ToolDecision(
            allowed=True,
            tool=call.name,
            reversibility=reversibility,
            taint=verdict.taint,
            tier=verdict.tier,
            evidence_doc_ids=verdict.matched_doc_ids,
            reason=(
                f"{call.name} is reversible, so untrusted influence is not worth a "
                f"freeze{undeclared_note}"
            ),
        )

    if taint_rank >= _UNTRUSTED_FLOOR:
        tier_note = (
            "traced to that content"
            if verdict.tier == 1
            else "not traced to any passage, but that content was in context"
        )
        return ToolDecision(
            allowed=False,
            tool=call.name,
            reversibility=reversibility,
            taint=verdict.taint,
            tier=verdict.tier,
            amount_inr=amount,
            cap_inr=cap,
            evidence_doc_ids=verdict.matched_doc_ids,
            reason=(
                f"{call.name} is {reversibility} and was influenced by {verdict.taint} "
                f"content ({tier_note}); frozen for human approval{undeclared_note}"
            ),
        )

    return ToolDecision(
        allowed=True,
        tool=call.name,
        reversibility=reversibility,
        taint=verdict.taint,
        tier=verdict.tier,
        amount_inr=amount,
        cap_inr=cap,
        evidence_doc_ids=verdict.matched_doc_ids,
        reason=(
            f"{call.name} is {reversibility} but was motivated by {verdict.taint} "
            f"content, which the customer or the bank controls{undeclared_note}"
        ),
    )


def describe_arguments(call: ToolCall, *, limit: int = 6) -> str:
    """A one-line argument summary for the review card."""
    parts = [f"{k}={v!r}" for k, v in list(call.arguments.items())[:limit]]
    if len(call.arguments) > limit:
        parts.append(f"... +{len(call.arguments) - limit} more")
    return ", ".join(parts)


def evidence_for_review(call: ToolCall, verdict: TaintVerdict) -> list[str]:
    """What a reviewer needs on the card: the taint story and the raw arguments."""
    lines = [verdict.rationale] if verdict.rationale else []
    lines.extend(f"argument value: {value}" for value in argument_strings(call.arguments)[:8])
    return lines
