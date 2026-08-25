"""The stakes model — one estimate, two budgets.

This is Contribution 1 in code: the router and the guardrail both read the `Stakes`
object this produces. Never fork them into separately tuned systems (invariant 1).

**Deterministic, by decision, not by expedience (ADR-005).** Stakes is the single number
driving both spend and scrutiny. If it were a black box, the governance story collapses:
"who decided this was worth Rs.40,000?" is the question the panel will ask, and "a model
did" is a losing answer. So it is a feature scorer over the policy file — auditable,
diffable, sub-millisecond, replayable, and reviewable by risk and compliance rather than
by an engineer.

Features, each contributing a readable line to `rationale`:

* the retrieved documents' domain (the dominant term — it comes from the policy)
* the largest monetary amount in play
* intent keywords in the user's turn
* the user's role
* the reversibility of any tool the request could reach
* conversation depth (a long thread that keeps failing is worth more, not less)

The output carries `features` so a decision can be replayed and audited exactly.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from interlock.core.money import format_inr
from interlock.core.policy import Policy
from interlock.core.types import Reversibility, Stakes
from interlock.signals.base import PreflightContext

__all__ = ["StakesModel", "largest_amount_inr"]

#: Rupee amounts: "Rs. 40,000", "INR 1,85,000", "₹12,400", "40000 rupees".
#: Handles the Indian grouping convention (1,85,000), which a naive \d{1,3}(,\d{3})* misses.
_AMOUNT = re.compile(
    r"(?:(?:rs\.?|inr|₹)\s*)([\d,]+(?:\.\d{1,2})?)"
    r"|([\d,]+(?:\.\d{1,2})?)\s*(?:rupees|rs\.?|inr)\b",
    re.IGNORECASE,
)

#: Lakh and crore, which appear constantly in Indian banking text and are worth
#: 10^5 and 10^7 -- a scorer that ignores them under-prices the highest-stakes requests.
_SCALED = re.compile(
    r"(?:(?:rs\.?|inr|₹)\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|crores)\b",
    re.IGNORECASE,
)

_SCALE = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}

#: Intent keywords -> the domain they suggest. One feature among several (ADR-005);
#: it never decides alone, and the retrieved domain outranks it.
_INTENT: dict[str, tuple[str, ...]] = {
    "prepayment": (
        "prepay",
        "prepayment",
        "foreclose",
        "foreclosure",
        "part payment",
        "pay off early",
    ),
    "loan_terms": ("loan", "emi", "tenure", "interest rate", "eligibility", "sanction", "mortgage"),
    "claims": ("claim", "insurance", "settle", "surveyor", "dispute", "unauthorised", "fraud"),
    "fees": ("fee", "charge", "penalty", "levy", "commission"),
    "payments": ("transfer", "neft", "rtgs", "imps", "upi", "remit", "send money", "payment"),
    "branch_info": ("branch", "open", "timing", "hours", "address", "ifsc", "atm", "location"),
}

#: A role that can act on an answer raises what being wrong costs.
_ROLE_MULTIPLIER = {
    "customer": 1.0,
    "agent": 1.2,
    "underwriter": 1.5,
    "admin": 2.0,
}


def largest_amount_inr(text: str) -> float:
    """The largest rupee amount mentioned, in rupees. 0.0 when none."""
    if not text:
        return 0.0
    amounts: list[float] = []

    for match in _SCALED.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        amounts.append(value * _SCALE[match.group(2).lower()])

    for match in _AMOUNT.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        try:
            amounts.append(float(raw.replace(",", "")))
        except ValueError:
            continue

    return max(amounts) if amounts else 0.0


@dataclass
class StakesModel:
    """Deterministic feature scorer. Sub-millisecond, and it explains itself."""

    policy: Policy
    name: str = "stakes"
    #: Conversation depth at which a thread is treated as struggling.
    depth_threshold: int = 3
    _intent: dict[str, tuple[str, ...]] = field(default_factory=lambda: _INTENT)

    def estimate(self, ctx: PreflightContext) -> Stakes:
        rationale: list[str] = []
        features: dict[str, float] = {}

        domain, domain_source = self._domain(ctx, rationale)
        entry = self.policy.domain(domain)
        impact = entry.impact_inr
        reversibility: Reversibility = entry.reversibility
        rationale.append(
            f"domain '{domain}' ({domain_source}) -> base {format_inr(impact)}, {reversibility}"
        )
        features["base_impact_inr"] = impact

        # Monetary magnitude. Uses the policy's own bands so the multiplier is
        # reviewable rather than invented here.
        amount = largest_amount_inr(ctx.last_user_message) or largest_amount_inr(ctx.all_text())
        features["monetary_amount_inr"] = amount
        if amount > 0:
            multiplier = self.policy.monetary_multiplier(amount)
            features["monetary_multiplier"] = multiplier
            if multiplier > 1.0:
                impact *= multiplier
                rationale.append(f"amount {format_inr(amount)} in play -> x{multiplier:g}")
            else:
                rationale.append(f"amount {format_inr(amount)} in play (below the first band)")

        # Tool reversibility. An answer that can reach an irreversible action is worth
        # far more to get wrong than the same words with no tool attached -- which is
        # why the interlock fires on calls a content filter would wave through.
        tool_reversibility, tool_name = self._tool_reversibility(ctx)
        if tool_reversibility is not None:
            features["tool_irreversible"] = 1.0 if tool_reversibility == "irreversible" else 0.0
            if _rank(tool_reversibility) > _rank(reversibility):
                rationale.append(
                    f"tool '{tool_name}' is {tool_reversibility} -> reversibility raised"
                )
                reversibility = tool_reversibility

        # User role.
        role_multiplier = _ROLE_MULTIPLIER.get(ctx.user_role, 1.0)
        features["role_multiplier"] = role_multiplier
        if role_multiplier != 1.0:
            impact *= role_multiplier
            rationale.append(f"role '{ctx.user_role}' -> x{role_multiplier:g}")

        # Conversation depth. A thread on its fourth attempt is a thread that is not
        # working; the rework it is about to cause is part of what being wrong costs.
        depth = ctx.conversation_depth
        features["conversation_depth"] = float(depth)
        if depth >= self.depth_threshold:
            impact *= 1.25
            rationale.append(f"conversation depth {depth} -> x1.25 (repeated attempts)")

        confidence = self._confidence(domain_source, ctx)
        features["impact_inr"] = impact

        return Stakes(
            impact_inr=round(impact, 2),
            reversibility=reversibility,
            domain=domain,
            confidence=confidence,
            rationale=rationale,
            features=features,
        )

    # -- features ---------------------------------------------------------- #

    def _domain(self, ctx: PreflightContext, rationale: list[str]) -> tuple[str, str]:
        """Retrieved documents outrank keywords.

        What was actually retrieved is evidence about what the question is about;
        keywords in the question are only a hint, and they are the part an attacker
        controls.
        """
        retrieved_domains = [fragment.doc_id for fragment in ctx.retrieved if fragment.doc_id]
        domains = [
            d
            for d in (getattr(f, "domain", None) for f in ctx.retrieved)
            if isinstance(d, str) and d
        ]
        if domains:
            return Counter(domains).most_common(1)[0][0], "retrieved documents"

        text = ctx.last_user_message.lower()
        scores = {
            domain: sum(1 for keyword in keywords if keyword in text)
            for domain, keywords in self._intent.items()
        }
        best = max(scores, key=lambda d: scores[d])
        if scores[best] > 0:
            return best, "intent keywords"

        if retrieved_domains:
            rationale.append("retrieved documents carried no domain label")
        return "general", "default"

    def _tool_reversibility(self, ctx: PreflightContext) -> tuple[Reversibility | None, str]:
        """The most irreversible tool this request could reach."""
        worst: Reversibility | None = None
        worst_name = ""
        for tool in ctx.tools:
            name = _tool_name(tool)
            if not name:
                continue
            reversibility = self.policy.tool(name).reversibility
            if worst is None or _rank(reversibility) > _rank(worst):
                worst, worst_name = reversibility, name
        return worst, worst_name

    def _confidence(self, domain_source: str, ctx: PreflightContext) -> float:
        """How sure the stakes model is -- shown in the console, never hidden.

        Low confidence does not mean low stakes. It means the estimate rests on weaker
        evidence, which is exactly what a reviewer needs to know.
        """
        if domain_source == "retrieved documents":
            return 0.9 if len(ctx.retrieved) >= 2 else 0.75
        if domain_source == "intent keywords":
            return 0.55
        return 0.25


def _rank(reversibility: Reversibility) -> int:
    return {"reversible": 0, "costly": 1, "irreversible": 2}[reversibility]


def _tool_name(tool: dict[str, object]) -> str:
    """Read a tool name from either OpenAI tool shape."""
    function = tool.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(tool.get("name") or "")
