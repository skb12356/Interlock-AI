"""The objective: convert every action into expected loss, in one currency.

    E[L(a)] =  SUM_d P(d) * Impact_d * (1 - eff[a][d])   (1) the harm that survives
            +  (1 - P(any)) * Nuisance(a)                (2) the cost of a false alarm
            +  tokens(a) * price                         (3) the compute
            +  lambda_time * delta_latency(a) / 1000     (4) the user's time, priced

Three details make this real rather than decorative (Implementation02 §4.2):

* **Impact_d is derived, not typed in.** It comes from the policy file — stakes impact,
  times the defect multiplier, times the reversibility multiplier. A reviewer diffs a
  YAML file, not a threshold buried in code.
* **eff[a][d] is measured, not guessed** (ADR-009). The policy carries the provenance of
  every number, so a prior can never be displayed as a measurement.
* **Hard constraints run before the argmin** (ADR-008). Deterministic rules short-circuit
  to L4/L5 with no model in the loop. The optimiser then chooses the cheapest action
  among those that remain. That is the difference between "a number picked the action"
  and "a number picked the cheapest action that still meets the guarantee".

Term (2) is the term that stops Interlock over-blocking, and over-blocking is what gets
guardrails switched off in week two.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from interlock.core.money import format_inr
from interlock.core.policy import Policy
from interlock.core.types import ACTIONS, Action, Defect, LossRow, Stakes

__all__ = [
    "ActionChoice",
    "HardRule",
    "choose_action",
    "p_any",
    "price_actions",
    "unavailable_actions",
]

#: Actions that cannot be taken once the sentence has already reached the user.
#: You cannot un-say something; the contract says so and the console shows it honestly.
_BLOCKED_ONCE_EMITTED: frozenset[Action] = frozenset({"L2_repair", "L3_reroute", "L5_block"})

#: Actions that put a human in the loop. Their time is an OPERATIONAL cost (term 3),
#: charged unconditionally -- the reviewer is paid whether or not the answer turns out
#: to have been fine. Charging it through the false-alarm term instead (weighted by
#: 1 - P(any)) made holding cost Rs.22 of human time at P=0.9 rather than Rs.220, which
#: made holding look nearly free at exactly the moment it was most likely to be chosen.
#: L5 is here too, and consistently so: a blocked customer still needs their question
#: answered by someone. The reasoning recorded against F-001 -- "blocking loses the
#: interaction AND still escalates to a human" -- only holds if the escalation is
#: actually priced. Charging it on hold but not on block would make refusing cheaper
#: than deferring, which is precisely backwards.
_REQUIRES_HUMAN: frozenset[Action] = frozenset({"L4_hold", "L5_block"})


@dataclass(frozen=True, slots=True)
class HardRule:
    """A deterministic rule that fired, short-circuiting the optimiser.

    A canary leak must not be a probability judgement, and a system whose worst case
    depends entirely on calibration quality is not one an enterprise switches on.
    """

    name: str
    action: Action
    reason: str


@dataclass(frozen=True, slots=True)
class ActionChoice:
    """The outcome of pricing: what was chosen, what came second, and by how much."""

    action: Action
    loss_table: list[LossRow]
    chosen_loss: float
    runner_up: Action | None
    margin: float
    why: list[str]
    hard_rule: str | None = None


def p_any(probs: Mapping[Defect, float]) -> float:
    """P(at least one defect), assuming independence across classes.

    Independence is an approximation and a documented one: the defect classes are not
    truly independent (an ungrounded sentence is likelier to be contradicted too), so
    this slightly *over*-estimates P(any) and therefore slightly *under*-charges the
    false-alarm term. That is the conservative direction for a safety system — it makes
    intervening look marginally cheaper, never more expensive than it is.
    """
    survival = 1.0
    for value in probs.values():
        survival *= 1.0 - min(max(value, 0.0), 1.0)
    return 1.0 - survival


def unavailable_actions(*, already_emitted: bool) -> dict[Action, str]:
    """Which rungs are off the ladder, and why.

    The ladder shrinks as the answer travels: not yet sent, every rung is available;
    streaming but uncommitted, L1-L3 still work because the gate is holding a sentence;
    already read, all you can do is annotate or notify.
    """
    if not already_emitted:
        return {}
    return {action: "already_emitted" for action in _BLOCKED_ONCE_EMITTED}


def price_actions(
    *,
    probs: Mapping[Defect, float],
    stakes: Stakes,
    policy: Policy,
    already_emitted: bool = False,
    monetary_amount_inr: float = 0.0,
    extra_unavailable: Mapping[Action, str] | None = None,
) -> list[LossRow]:
    """Price every rung of the ladder. Always returns a complete table.

    A row is returned even for an action that cannot be taken, carrying the reason —
    the table *is* the explanation, so an action the optimiser could not choose must
    still show why it could not.
    """
    unavailable = dict(unavailable_actions(already_emitted=already_emitted))
    if extra_unavailable:
        unavailable.update(extra_unavailable)

    probability_of_any = p_any(probs)
    price_per_token = policy.price_inr_per_1k_tokens / 1000.0

    rows: list[LossRow] = []
    for action in ACTIONS:
        residual_harm = sum(
            probability
            * policy.impact_for(
                impact_inr=stakes.impact_inr,
                defect=defect,
                reversibility=stakes.reversibility,
                monetary_amount_inr=monetary_amount_inr,
            )
            * (1.0 - policy.efficacy_for(action, defect))
            for defect, probability in probs.items()
        )
        nuisance = (1.0 - probability_of_any) * policy.nuisance_inr[action]
        compute = policy.compute_tokens[action] * price_per_token
        if action in _REQUIRES_HUMAN:
            compute += policy.human_review.cost_inr
        latency = policy.lambda_time_inr_per_second * (policy.latency_ms[action] / 1000.0)

        reason = unavailable.get(action)
        rows.append(
            LossRow(
                action=action,
                residual_harm=residual_harm,
                nuisance=nuisance,
                compute=compute,
                latency=latency,
                total=residual_harm + nuisance + compute + latency,
                available=reason is None,
                unavailable_reason=reason,
            )
        )
    return rows


def choose_action(
    *,
    probs: Mapping[Defect, float],
    stakes: Stakes,
    policy: Policy,
    already_emitted: bool = False,
    monetary_amount_inr: float = 0.0,
    hard_rules: Sequence[HardRule] = (),
    extra_unavailable: Mapping[Action, str] | None = None,
) -> ActionChoice:
    """Hard constraints first, then the cheapest available action.

    The loss table is computed and returned **either way**. Even when a deterministic
    rule decides the outcome, the table shows what the arithmetic would have said —
    which is what lets the console explain a hard stop rather than merely announce it.
    """
    rows = price_actions(
        probs=probs,
        stakes=stakes,
        policy=policy,
        already_emitted=already_emitted,
        monetary_amount_inr=monetary_amount_inr,
        extra_unavailable=extra_unavailable,
    )
    by_action = {row.action: row for row in rows}

    # (1) Hard constraints, before the argmin. No model in the loop.
    fired = _strongest_rule(hard_rules)
    if fired is not None:
        return ActionChoice(
            action=fired.action,
            loss_table=rows,
            chosen_loss=by_action[fired.action].total,
            runner_up=None,
            margin=0.0,
            why=[f"hard rule: {fired.reason}"],
            hard_rule=fired.name,
        )

    # (2) The argmin, over available actions only.
    available = sorted(
        (row for row in rows if row.available),
        key=lambda row: (row.total, ACTIONS.index(row.action)),
    )
    if not available:
        # Cannot happen with the current rules -- L0 and L1 always survive -- but the
        # engine must never raise, so degrade to passing rather than to an exception.
        return ActionChoice(
            action="L0_pass",
            loss_table=rows,
            chosen_loss=by_action["L0_pass"].total,
            runner_up=None,
            margin=0.0,
            why=["degraded: no action was available"],
        )

    best = available[0]
    runner_up = available[1] if len(available) > 1 else None

    # F-019: an intervention must improve materially on passing, not merely win the
    # argmin by a few rupees. This abstention rule only applies when L0 is available;
    # hard rules returned above and conformal/action constraints remain authoritative.
    pass_row = by_action["L0_pass"]
    if (
        best.action != "L0_pass"
        and pass_row.available
        and policy.minimum_relative_action_gain > 0.0
    ):
        relative_gain = (pass_row.total - best.total) / max(pass_row.total, 1.0)
        if relative_gain < policy.minimum_relative_action_gain:
            return ActionChoice(
                action="L0_pass",
                loss_table=rows,
                chosen_loss=pass_row.total,
                runner_up=best.action,
                margin=0.0,
                why=[
                    f"stakes: {stakes.domain} at {format_inr(stakes.impact_inr)} "
                    f"({stakes.reversibility})",
                    f"policy margin: {best.action} reduces expected loss by "
                    f"{relative_gain:.1%}, below the required "
                    f"{policy.minimum_relative_action_gain:.1%}; choosing L0_pass",
                ],
            )

    return ActionChoice(
        action=best.action,
        loss_table=rows,
        chosen_loss=best.total,
        runner_up=runner_up.action if runner_up else None,
        margin=(runner_up.total - best.total) if runner_up else 0.0,
        why=_explain(best, runner_up, probs, stakes),
    )


def _strongest_rule(rules: Sequence[HardRule]) -> HardRule | None:
    """When several deterministic rules fire, the most drastic one wins."""
    if not rules:
        return None
    return max(rules, key=lambda rule: ACTIONS.index(rule.action))


def _explain(
    best: LossRow,
    runner_up: LossRow | None,
    probs: Mapping[Defect, float],
    stakes: Stakes,
) -> list[str]:
    """Ordered, human-readable. The console renders these verbatim."""
    why = [
        f"stakes: {stakes.domain} at {format_inr(stakes.impact_inr)} ({stakes.reversibility})",
    ]
    ranked = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    for defect, probability in ranked[:3]:
        if probability > 0.0:
            why.append(f"P({defect}) = {probability:.2f}")
    why.append(
        f"{best.action} costs {format_inr(best.total, 2)} "
        f"(harm {best.residual_harm:,.2f} + nuisance {best.nuisance:,.2f} "
        f"+ compute {best.compute:,.2f} + latency {best.latency:,.2f})"
    )
    if runner_up is not None:
        why.append(
            f"next best {runner_up.action} at {format_inr(runner_up.total, 2)} "
            f"(margin {format_inr(runner_up.total - best.total, 2)})"
        )
    return why
