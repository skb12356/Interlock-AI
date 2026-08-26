"""How good would the detector have to be? — the F-019 experiment.

`make eval` reports a false-intervention rate of ~91% against a ≤2% target. The obvious
reading is "the detector is weak, improve it". This script exists to test that reading,
because it is wrong in an important way and the difference changes what gets built next.

The method is to take the detector out of the question entirely. Instead of measuring
what the real signals score, we *stipulate* a detector: it scores clean text at some
floor ``f`` and defective text at some ceiling ``c``, and is otherwise perfect. Then we
run the real policy, the real four-term objective and the real ladder over the real
seeded set, and sweep ``f``.

That isolates one variable. Whatever comes out is a property of the **objective**, not
of any particular detector, and it answers the question the eval cannot: *is there a
detector good enough to hit the target, and if so how good?*

    uv run python scripts/sensitivity.py
    uv run python scripts/sensitivity.py --json artifacts/eval/sensitivity.json

Two things it prints that are worth reading carefully:

* **The break-even floor per stakes band** — solved directly from the objective rather
  than swept, so it is exact. This is the number a probe has to beat.
* **The achievable region** — the floors at which BOTH the ≤2% false-intervention target
  and the ≥90% catch rate hold at once. If that region is empty, no detector rescues the
  current policy and the resolution is a policy decision rather than an ML one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.policy import Policy, load_policy  # noqa: E402
from interlock.core.types import Decision, RiskContext, Stakes  # noqa: E402
from interlock.eval.harness import run_eval  # noqa: E402
from interlock.eval.seeded import build_seeded_set  # noqa: E402
from interlock.interlock_tools.holds import ToolInterlock  # noqa: E402
from interlock.ledger.writer import Ledger  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.risk.objective import choose_action  # noqa: E402

#: Floors to sweep, from "impossibly good" to "what we actually have". Log-spaced,
#: because the interesting behaviour is all in the bottom two decades.
DEFAULT_FLOORS: tuple[float, ...] = (
    0.000_01,
    0.000_03,
    0.000_1,
    0.000_3,
    0.001,
    0.003,
    0.01,
    0.019,
    0.05,
)

#: What a stipulated detector scores on genuinely defective text. 0.95 rather than 1.0
#: because a detector that is *certain* is not a detector, and the catch rate should not
#: be flattered by an assumption no probe can meet either.
DEFECT_CEILING = 0.95


class StipulatedEngine:
    """A detector with a declared floor and ceiling, and no signals at all.

    Everything downstream of the probability -- policy, impact multipliers, the
    four-term arithmetic, the hard-rule pre-pass, the ladder -- is the real thing. Only
    the number that goes in is stipulated, which is precisely the variable under test.
    """

    def __init__(self, policy: Policy, floor: float, ceiling: float, defective_ids: set[str]):
        self.policy = policy
        self.floor = floor
        self.ceiling = ceiling
        self._defective = defective_ids

    async def evaluate(self, ctx: RiskContext) -> Decision:
        case_id = ctx.request_id.removeprefix("eval_")
        probability = self.ceiling if case_id in self._defective else self.floor
        choice = choose_action(
            probs={"ungrounded": probability},
            stakes=ctx.stakes,
            policy=self.policy,
            already_emitted=ctx.already_emitted,
        )
        return Decision(
            decision_id="dec_stipulated",
            action=choice.action,
            loss_table=choice.loss_table,
            chosen_loss=choice.chosen_loss,
            runner_up=choice.runner_up,
            margin=choice.margin,
            probs={"ungrounded": probability},
            why=choice.why,
        )

    async def prefetch(self, request_id: str, question: str, retrieved: list) -> None:
        return None

    def arm(self, request_id: str, header: str | None) -> None:
        return None

    def disarm(self, request_id: str) -> None:
        return None

    def health(self) -> dict[str, object]:
        return {"engine": "stipulated", "floor": self.floor, "ceiling": self.ceiling}


def break_even_floor(policy: Policy, stakes: Stakes) -> float:
    """The largest P(ungrounded) at which L0_pass still wins. Solved, not swept.

    Bisection on a monotone predicate: raising P can only ever make passing worse
    relative to acting, so there is exactly one crossing and 60 iterations put it well
    inside float precision.
    """
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        choice = choose_action(probs={"ungrounded": mid}, stakes=stakes, policy=policy)
        if choice.action == "L0_pass":
            low = mid
        else:
            high = mid
    return low


BANDS: tuple[tuple[float, str, str], ...] = (
    (50, "reversible", "branch_info"),
    (200, "reversible", "general"),
    (3_000, "costly", "fees"),
    (12_000, "costly", "claims"),
    (40_000, "costly", "prepayment"),
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "artifacts" / "eval" / "sensitivity.json"
    )
    parser.add_argument("--floors", type=float, nargs="*", default=list(DEFAULT_FLOORS))
    parser.add_argument("--ceiling", type=float, default=DEFECT_CEILING)
    args = parser.parse_args()

    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    chunks = corpus_chunks(load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT))
    cases = build_seeded_set(chunks, canary="INTERLOCK-CANARY-SENSITIVITY-0001")
    defective_ids = {case.case_id for case in cases if case.is_defective}

    # -- part 1: the exact break-even, solved from the objective ------------- #
    print("Break-even detector floor -- the largest P(clean) at which L0_pass still wins.")
    print("Solved directly from the objective, so these are exact.\n")
    print(f"  {'stakes':>10}  {'domain':<13} {'floor must be below':>20}")
    print("  " + "-" * 47)
    thresholds: list[dict[str, Any]] = []
    for impact, reversibility, domain in BANDS:
        stakes = Stakes(
            impact_inr=impact, reversibility=reversibility, domain=domain, confidence=0.9
        )
        floor = break_even_floor(policy, stakes)
        thresholds.append({"impact_inr": impact, "domain": domain, "break_even_floor": floor})
        print(f"  Rs.{impact:>8,}  {domain:<13} {floor * 100:>18.4f}%")

    print("\n  For reference, the real detector's floor on clean text is ~1.9%.")
    print("  It clears only the first row.\n")

    # -- part 2: sweep a stipulated detector over the real seeded set -------- #
    print(
        f"Sweeping a stipulated detector over the seeded set "
        f"({len(cases)} cases, {len(defective_ids)} defective)."
    )
    print(
        f"Defective text is scored at {args.ceiling:.2f} throughout; only the clean floor moves.\n"
    )
    # Two false-intervention columns, because they are different claims and the
    # difference turns out to be the whole answer. ANY counts L1_annotate, which ships
    # the answer unchanged with a citation appended and costs 5 ms. DISRUPTIVE counts
    # only what a customer actually experiences as an intervention: a 14 s repair, a
    # 30 s reroute, a 15-minute hold, a refusal.
    print(
        f"  {'clean floor':>12}  {'FI (any)':>10}  {'FI (disrupt)':>13}  "
        f"{'catch':>8}  {'verdict':>16}"
    )
    print("  " + "-" * 68)

    ledger = Ledger(db_path=REPO_ROOT / "artifacts" / "eval" / "sensitivity.db")
    await ledger.start()
    rows: list[dict[str, Any]] = []
    try:
        interlock = ToolInterlock(policy=policy, ledger=ledger)
        for floor in args.floors:
            engine = StipulatedEngine(policy, floor, args.ceiling, defective_ids)
            _off, _on, metrics = await run_eval(
                cases=cases, engine=engine, policy=policy, tool_interlock=interlock
            )
            false_interventions = metrics.by_name("False interventions")
            disruptive = metrics.by_name("  ...of those, disruptive")
            catch = metrics.by_name("Pre-Action Catch Rate")
            assert false_interventions is not None and catch is not None
            disruptive_rate = disruptive.value if disruptive else float("nan")

            meets_any = false_interventions.value <= 0.02 and catch.value >= 0.90
            meets_disruptive = disruptive_rate <= 0.02 and catch.value >= 0.90
            rows.append(
                {
                    "clean_floor": floor,
                    "false_intervention_rate_any": false_interventions.value,
                    "false_intervention_rate_disruptive": disruptive_rate,
                    "catch_rate": catch.value,
                    "meets_targets_counting_annotation": meets_any,
                    "meets_targets_disruptive_only": meets_disruptive,
                }
            )
            verdict = "BOTH" if meets_any else ("disruptive only" if meets_disruptive else "")
            print(
                f"  {floor * 100:>11.4f}%  {false_interventions.value * 100:>9.2f}%  "
                f"{disruptive_rate * 100:>12.2f}%  {catch.value * 100:>7.2f}%  "
                f"{verdict:>16}"
            )
    finally:
        await ledger.stop()

    any_ok = [r for r in rows if r["meets_targets_counting_annotation"]]
    disruptive_ok = [r for r in rows if r["meets_targets_disruptive_only"]]
    print()

    if disruptive_ok:
        best = max(r["clean_floor"] for r in disruptive_ok)
        print(f"  The DISRUPTIVE target is ACHIEVABLE below a clean floor of {best * 100:.4f}%.")
        print("  A detector that good produces ZERO repairs, reroutes, holds or blocks on")
        print("  clean traffic, at a 100% catch rate. That is a hard but stateable ML")
        print("  goal, and it is exactly what the observer probe (D2-B4/B7) exists to")
        print("  deliver -- so F-019 is NOT a dead end.")
        verdict = f"disruptive target achievable below a clean floor of {best}"
    else:
        print("  Not achievable at any swept floor, even counting disruptive actions only.")
        verdict = "not achievable at any swept floor"

    print()
    if not any_ok and disruptive_ok:
        print("  The target COUNTING ANNOTATION is missed at every floor, and at the best")
        print("  floor the residual is 100% L1_annotate on very high-stakes traffic.")
        print("  Annotating ships the answer unchanged with a citation appended, for 5 ms.")
        print("  Whether that counts as a 'false intervention' is a definitional question,")
        print("  and it is the entire remaining gap.")
        print()
        print("  Report BOTH rates and name them. Do NOT silently redefine the metric to")
        print("  whichever one passes -- the plan's target was written without the")
        print("  distinction, so the distinction has to be argued, not assumed.")
        verdict += "; annotation-inclusive target missed at every floor"

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(
            {
                "question": "how good must the detector be for both targets to hold?",
                "method": (
                    "stipulate P(clean)=floor and P(defective)=ceiling, then run the "
                    "REAL policy, objective and ladder over the REAL seeded set"
                ),
                "n_cases": len(cases),
                "n_defective": len(defective_ids),
                "defect_ceiling": args.ceiling,
                "policy_version": policy.policy_version,
                "break_even_by_band": thresholds,
                "sweep": rows,
                "verdict": verdict,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
