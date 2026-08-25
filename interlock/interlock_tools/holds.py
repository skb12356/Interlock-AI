"""Freezing a tool call, durably.

The product boundary. Anyone can refuse a tool call in memory; the claim an enterprise
is actually buying is *kill the process mid-hold, restart it, and the review card is
still there*. So the hold is written and committed before the caller is told the call
was frozen -- not queued, not fire-and-forget. If the write fails, the call stays
frozen and the failure is surfaced: a hold nobody recorded is a call nobody will ever
approve, and silently allowing it instead would turn a storage error into an
irreversible action.

Three pieces:

* :class:`ToolInterlock` -- evaluate, and on a freeze persist the review card.
* **Resume tokens** -- an unguessable handle the approver presents to release the call.
  The hold id is in the console URL and the logs; the token is not, so knowing which
  hold exists is not the same as being able to release it.
* **The expiry sweeper** -- a hold nobody answered inside the SLA becomes ``expired``,
  never ``approved``. Silence is not consent, least of all for an irreversible action.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from interlock.core.clock import wall_time
from interlock.core.ids import new_hold_id
from interlock.core.policy import Policy
from interlock.core.types import Fragment, Provenance
from interlock.interlock_tools.provenance import (
    ToolCall,
    conversation_taint,
    influencing_taint,
)
from interlock.interlock_tools.reversibility import (
    ToolDecision,
    describe_arguments,
    evaluate_tool_call,
    evidence_for_review,
)

__all__ = ["HeldToolCall", "ToolInterlock", "new_resume_token"]

#: 32 bytes of urandom. Long enough that guessing is not a strategy, short enough to
#: paste into a curl command during a demo.
RESUME_TOKEN_BYTES = 32


def new_resume_token() -> str:
    return secrets.token_urlsafe(RESUME_TOKEN_BYTES)


@dataclass(frozen=True, slots=True)
class HeldToolCall:
    """What the reviewer sees, and what a resume needs."""

    hold_id: str
    resume_token: str
    call: ToolCall
    decision: ToolDecision
    sla_deadline_ts: float

    def review_card(self) -> dict[str, Any]:
        """The console payload. Explains a decision already made (invariant 2)."""
        return {
            "hold_id": self.hold_id,
            "tool": self.call.name,
            "arguments": describe_arguments(self.call),
            "reversibility": self.decision.reversibility,
            "taint": self.decision.taint,
            "attribution_tier": self.decision.tier,
            "reason": self.decision.reason,
            "evidence_doc_ids": list(self.decision.evidence_doc_ids),
            "amount_inr": self.decision.amount_inr,
            "sla_deadline_ts": self.sla_deadline_ts,
        }


@dataclass
class ToolInterlock:
    """Evaluates tool calls and freezes the ones that must not run unsupervised."""

    policy: Policy
    ledger: Any
    #: Running taint per request. A conversation does not get clean again because the
    #: next turn happened to retrieve nothing.
    _taint: dict[str, Provenance] = field(default_factory=dict, init=False)

    def observe(self, request_id: str, fragments: Sequence[Fragment]) -> Provenance:
        """Fold this turn's context into the request's running taint."""
        current = conversation_taint(fragments, prior=self._taint.get(request_id, "system"))
        self._taint[request_id] = current
        return current

    def forget(self, request_id: str) -> None:
        """Drop a finished request's taint so a long-lived process does not accumulate."""
        self._taint.pop(request_id, None)

    def evaluate(
        self,
        call: ToolCall,
        fragments: Sequence[Fragment],
        *,
        request_id: str,
    ) -> ToolDecision:
        """Decide, without persisting. Deterministic and side-effect free."""
        verdict = influencing_taint(
            call, fragments, conversation_taint=self._taint.get(request_id, "system")
        )
        return evaluate_tool_call(call, verdict, self.policy)

    async def check(
        self,
        call: ToolCall,
        fragments: Sequence[Fragment],
        *,
        request_id: str,
    ) -> tuple[ToolDecision, HeldToolCall | None]:
        """Decide, and durably record the hold if the call is frozen.

        Returns ``(decision, held_or_None)``. The hold is committed **before** this
        returns, so a caller that sees ``held`` can rely on the review card existing.
        """
        verdict = influencing_taint(
            call, fragments, conversation_taint=self._taint.get(request_id, "system")
        )
        decision = evaluate_tool_call(call, verdict, self.policy)
        if decision.allowed:
            return decision, None

        hold = HeldToolCall(
            hold_id=new_hold_id(),
            resume_token=new_resume_token(),
            call=call,
            decision=decision,
            sla_deadline_ts=wall_time() + self.policy.human_review.sla_minutes * 60.0,
        )
        await self.ledger.persist_hold(
            hold_id=hold.hold_id,
            request_id=request_id,
            kind="tool_call",
            payload={
                "tool": call.name,
                "arguments": call.arguments,
                "call_id": call.call_id,
                "reversibility": decision.reversibility,
                "taint": decision.taint,
                "attribution_tier": decision.tier,
                "amount_inr": decision.amount_inr,
                "cap_inr": decision.cap_inr,
            },
            evidence=evidence_for_review(call, verdict),
            flagged_span=call.name,
            reason=decision.reason,
            resume_token=hold.resume_token,
            sla_deadline_ts=hold.sla_deadline_ts,
        )
        return decision, hold

    async def resolve(
        self,
        hold_id: str,
        *,
        state: str,
        resolved_by: str,
        resume_token: str | None = None,
    ) -> tuple[bool, str]:
        """Approve or reject. Returns ``(ok, reason_if_not)``.

        The resume token is checked for **approval only**. Rejecting a hold is a safe
        action -- worst case someone cancels a call that would have been fine -- and
        requiring a secret to say "no" would mean a reviewer who lost the token cannot
        stop a pending irreversible action. Releasing one needs the token.
        """
        if state not in {"approved", "rejected"}:
            return False, f"unknown state {state!r}"

        record = self.find(hold_id)
        if record is None:
            return False, "no pending hold with that id"

        if state == "approved":
            expected = record.get("resume_token")
            # Compared in constant time: a timing oracle on this comparison is a way to
            # discover a token one byte at a time, and the token is what releases an
            # irreversible action.
            if not expected or not resume_token:
                return False, "approval requires the resume token"
            if not secrets.compare_digest(str(expected), str(resume_token)):
                return False, "resume token does not match"
            deadline = record.get("sla_deadline_ts")
            if deadline and wall_time() > float(deadline):
                return False, "the review window has expired; re-submit the request"

        ok = await self.ledger.resolve_hold(hold_id, state=state, resolved_by=resolved_by)
        return (True, "") if ok else (False, "the hold was already resolved")

    def find(self, hold_id: str) -> dict[str, Any] | None:
        for record in self.ledger.pending_holds():
            if record.get("hold_id") == hold_id:
                found: dict[str, Any] = record
                return found
        return None

    def pending_cards(self) -> list[dict[str, Any]]:
        """The review queue. Resume tokens are **not** included -- see the note below."""
        return [
            {
                "hold_id": record.get("hold_id"),
                "request_id": record.get("request_id"),
                "kind": record.get("kind"),
                # Always "pending" today, since the queue only lists pending holds.
                # Carried anyway: the field is not a secret, and a caller that has
                # to infer state from which endpoint it called will get it wrong the
                # first time the queue learns to show resolved holds too.
                "state": record.get("state"),
                "reason": record.get("reason"),
                "created_ts": record.get("created_ts"),
                "sla_deadline_ts": record.get("sla_deadline_ts"),
                "expired": _is_expired(record),
            }
            for record in self.ledger.pending_holds()
        ]

    async def sweep_expired(self) -> list[str]:
        """Expire holds nobody answered in time.

        Expired, never approved. Silence is not consent -- least of all for an
        irreversible action, which is the only kind that gets held on taint at all.
        """
        expired: list[str] = []
        for record in self.ledger.pending_holds():
            if not _is_expired(record):
                continue
            hold_id = str(record.get("hold_id"))
            if await self.ledger.resolve_hold(hold_id, state="expired", resolved_by="sweeper"):
                expired.append(hold_id)
        return expired


def _is_expired(record: dict[str, Any]) -> bool:
    deadline = record.get("sla_deadline_ts")
    if deadline is None:
        # No deadline means no expiry. A hold without an SLA waits forever rather than
        # being swept, which is the safe direction: sweeping it would release nothing,
        # but it would also make an un-triaged irreversible action disappear from the
        # queue that was supposed to surface it.
        return False
    return wall_time() > float(deadline)
