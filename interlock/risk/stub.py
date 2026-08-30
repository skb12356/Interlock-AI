"""The stub risk engine — the unblocking trick.

This ships **before** anything else in the Signals & Decisions stream so that the whole
enforcement path (streaming, the commit gate, repair, holds, the tool interlock, the
console) can be built and tested with **no GPU, no model weights, and no detectors**.

It is a stub in exactly one respect: the probabilities are scripted rather than
measured. Everything else is real — the policy file, the four-term arithmetic, the
hard-constraint pre-pass, the full loss table, the versions stamped on the decision. So
when ``RealRiskEngine`` replaces it at D3-B4, the swap is one line of dependency wiring
and the enforcement path does not notice.

Driven by a request header::

    X-Interlock-Force: ungrounded@2          # P(ungrounded)=0.9 on sentence 2
    X-Interlock-Force: contradicted@1:0.55   # an explicit probability
    X-Interlock-Force: canary_leak@3         # fires the deterministic hard rule

Keeping the scripting in a header rather than in the code means a demo, a test and a
chaos run all drive it the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from interlock.core.clock import Deadline
from interlock.core.ids import inputs_digest, new_decision_id
from interlock.core.policy import Policy
from interlock.core.types import DEFECTS, Decision, Defect, Fragment, RiskContext
from interlock.risk.objective import HardRule, choose_action

__all__ = ["FORCE_HEADER", "ForceDirective", "StubRiskEngine"]

FORCE_HEADER = "x-interlock-force"

#: Defects that a deterministic rule catches, so the stub can exercise the hard-rule
#: path (ADR-008) rather than only the optimiser.
_HARD_RULES: dict[Defect, HardRule] = {
    "canary_leak": HardRule(
        name="canary_leak",
        action="L5_block",
        reason="canary token matched on egress",
    ),
    "unsafe_action": HardRule(
        name="untrusted_irreversible_tool",
        action="L4_hold",
        reason="irreversible action x untrusted provenance",
    ),
}

#: What a forced defect's probability is when the header does not say.
_DEFAULT_FORCED_PROB = 0.9


@dataclass(frozen=True, slots=True)
class ForceDirective:
    """A parsed ``X-Interlock-Force`` header."""

    defect: Defect
    sentence_idx: int
    prob: float = _DEFAULT_FORCED_PROB

    @classmethod
    def parse(cls, raw: str) -> ForceDirective | None:
        """Parse ``<defect>@<sentence_idx>[:<prob>]``.

        Returns None on anything unparseable rather than raising: a malformed debug
        header must never be able to fail a request.
        """
        text = raw.strip()
        if "@" not in text:
            return None
        defect_part, _, rest = text.partition("@")
        index_part, _, prob_part = rest.partition(":")

        defect = defect_part.strip()
        if defect not in DEFECTS:
            return None
        try:
            sentence_idx = int(index_part)
            prob = float(prob_part) if prob_part else _DEFAULT_FORCED_PROB
        except ValueError:
            return None
        if not 0.0 <= prob <= 1.0:
            return None
        return cls(defect=defect, sentence_idx=sentence_idx, prob=prob)  # type: ignore[arg-type]


@dataclass
class StubRiskEngine:
    """Scripted probabilities, real everything-else. Satisfies the ``RiskEngine`` Protocol."""

    policy: Policy
    probe_version: str = "stub"
    calib_version: str = "stub"
    _armed: dict[str, ForceDirective] = field(default_factory=dict)
    _prefetched: set[str] = field(default_factory=set)

    # -- stub-only control surface (not part of Contract 1) ---------------- #

    def arm(self, request_id: str, header_value: str | None) -> ForceDirective | None:
        """Register a force directive for one request, from its header."""
        if not header_value:
            return None
        directive = ForceDirective.parse(header_value)
        if directive is not None:
            self._armed[request_id] = directive
        return directive

    def disarm(self, request_id: str) -> None:
        """Called when the request finishes, so a long-lived process does not leak."""
        self._armed.pop(request_id, None)
        self._prefetched.discard(request_id)

    # -- Contract 1 -------------------------------------------------------- #

    async def evaluate(self, ctx: RiskContext) -> Decision:
        """Price the ladder and return the argmin. Never raises."""
        deadline = Deadline(budget_ms=ctx.remaining_deadline_ms)
        try:
            return self._evaluate(ctx, deadline)
        except Exception as exc:
            return self._degraded(ctx, f"stub engine error: {exc!r}", deadline)

    def _evaluate(self, ctx: RiskContext, deadline: Deadline) -> Decision:
        directive = self._armed.get(ctx.request_id)

        # An unforced sentence reports *no* defect probability, because the stub has no
        # detectors -- it is not asserting the sentence is clean, it has nothing to say.
        #
        # This matters more than it looks. Any non-zero baseline at high stakes makes an
        # intervention win the argmin: at Rs.40,000 impact, even P=0.001 puts Rs.100 of
        # expected harm against a repair that costs Rs.2.18 and removes 80% of it, so the
        # optimiser repairs every sentence. That is arithmetically correct and
        # operationally unusable, and it is exactly what the false-intervention target
        # (<= 2%) exists to discipline. The real defences are the conformal feasibility
        # filter (D3-B1) and measured efficacy (D3-B6); see IMPLEMENTATION_STATUS.md.
        probs: dict[Defect, float] = {}
        hard_rules: list[HardRule] = []
        why_prefix: list[str] = []

        if directive is not None and directive.sentence_idx == ctx.sentence_idx:
            probs = {directive.defect: directive.prob}
            why_prefix.append(
                f"forced by {FORCE_HEADER}: {directive.defect}@{directive.sentence_idx}"
            )
            rule = _HARD_RULES.get(directive.defect)
            if rule is not None:
                hard_rules.append(rule)

        choice = choose_action(
            probs=probs,
            stakes=ctx.stakes,
            policy=self.policy,
            adjustment=self.policy.decision_adjustment,
            already_emitted=ctx.already_emitted,
            hard_rules=hard_rules,
        )

        return Decision(
            decision_id=new_decision_id(),
            action=choice.action,
            loss_table=choice.loss_table,
            chosen_loss=choice.chosen_loss,
            runner_up=choice.runner_up,
            margin=choice.margin,
            probs=probs,
            why=why_prefix + choice.why,
            hard_rule=choice.hard_rule,
            repair_hint=None,  # the stub has no verifier, so it has no span to aim at
            policy_version=self.policy.policy_version,
            calib_version=self.calib_version,
            probe_version=self.probe_version,
            inputs_digest=inputs_digest(
                {
                    "request_id": ctx.request_id,
                    "sentence_idx": ctx.sentence_idx,
                    "sentence": ctx.sentence,
                    "question": ctx.question,
                    "stakes": ctx.stakes.model_dump(),
                    "already_emitted": ctx.already_emitted,
                }
            ),
            latency_ms=deadline.elapsed_ms,
        )

    def _degraded(self, ctx: RiskContext, reason: str, deadline: Deadline) -> Decision:
        """The shape the contract mandates on any internal failure.

        This is the last line of defence, so it touches nothing that could itself fail:
        the policy may be the very thing that was broken.
        """
        return Decision(
            decision_id=new_decision_id(),
            action="L0_pass",
            loss_table=[],
            chosen_loss=0.0,
            why=[f"degraded: {reason}"],
            policy_version=getattr(self.policy, "policy_version", "unknown"),
            calib_version=self.calib_version,
            probe_version=self.probe_version,
            latency_ms=deadline.elapsed_ms,
        )

    async def prefetch(
        self,
        request_id: str,
        question: str,
        retrieved: list[Fragment],
    ) -> None:
        """No weights to warm; recorded so the gateway's call path is still exercised."""
        self._prefetched.add(request_id)

    def health(self) -> dict[str, object]:
        return {
            "engine": "stub",
            "ok": True,
            "policy_version": self.policy.policy_version,
            "probe_version": self.probe_version,
            "calib_version": self.calib_version,
            "armed_requests": len(self._armed),
        }
