"""Fit the calibration layer and certify a threshold. Writes the artefacts.

    uv run python scripts/calibrate.py
    uv run python scripts/calibrate.py --items 4000 --alpha 0.01 --delta 0.10

Produces, in ``artifacts/calibration/``:

* ``calibrator.json``   -- isotonic maps + fusion weights, as plain data
* ``report.json``       -- out-of-fold ECE, Brier, AUROC, per-mode breakdown
* ``reliability.png``   -- the diagram the plan asks to ship with the ECE
* ``lambda.json``       -- the certified threshold and the sentence it licenses

Two properties of this script are the point of it:

**The reported numbers are out-of-fold.** ``fit`` produces the model that ships;
``evaluate`` produces the numbers, from calibrators that never saw the item they are
scoring. They are separate calls because conflating them is how an in-sample ECE of
0.004 ends up on a slide.

**The conformal step is allowed to fail.** If no threshold can be certified at the
requested alpha and delta, the script says so and exits non-zero. It does not widen
alpha until something passes. A guarantee that was adjusted until it held is not a
guarantee.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: A named constant because this file is edited by scripts, and a bare escape in a
#: patched string literal has been mangled into a real newline more than once.
NEWLINE = "\n"

from interlock.core.policy import load_policy  # noqa: E402
from interlock.eval.induce import TripleGenerator  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.risk.calibration import (  # noqa: E402
    MultiDefectCalibrator,
    SignalCalibrator,
    build_report,
)
from interlock.risk.conformal import select_threshold  # noqa: E402
from interlock.signals.grounding import GROUNDING_SIGNALS, grounding_signals  # noqa: E402


def _probe_features(triples: list[Any], model: str | None) -> np.ndarray | None:
    """Encode every triple through the observer probe, or return None if unavailable.

    Slow -- ~123 ms per item on CPU -- so it is opt-in. Worth the wait once: the whole
    question is whether a learned probe moves the CLEAN-TEXT FLOOR, which is what F-019
    turns on, and AUROC does not answer that.
    """
    from interlock.observer.encoder import ObserverEncoder
    from interlock.observer.probes import ProbeBundle

    path = REPO_ROOT / "artifacts" / "probes" / "probe.json"
    if not path.exists():
        print("  ! no trained probe at artifacts/probes/probe.json -- run scripts/train_probes.py")
        return None

    bundle = ProbeBundle.load(path)
    encoder = ObserverEncoder(model_name=model or bundle.model_name)
    # Untrusted passages are excluded from the premise, for the same reason as in
    # signals/probe_signal.py: with a poisoned document in the premise the attacker's
    # own claim genuinely IS entailed, and the probe would faithfully report so.
    premises = [
        NEWLINE.join(f.text for f in t.context if not str(f.provenance).endswith("untrusted"))
        or "(no context retrieved)"
        for t in triples
    ]
    batch = encoder.encode(premises, [t.answer for t in triples], batch_size=24)
    return bundle.score(batch.layers[bundle.best_layer]).reshape(-1, 1)


def build_dataset(items: int, seed: int) -> tuple[np.ndarray, np.ndarray, list[str], list[Any]]:
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    generator = TripleGenerator(chunks=corpus_chunks(documents), seed=seed)
    triples = generator.generate(items)
    if generator.fallbacks:
        print(f"  note: modes that could not be built: {generator.fallbacks}")

    features = np.array(
        [
            list(
                grounding_signals(triple.answer, triple.context, question=triple.question)
                .as_features()
                .values()
            )
            for triple in triples
        ]
    )
    labels = np.array([int(triple.is_defective) for triple in triples])
    modes = [triple.failure_mode for triple in triples]
    return features, labels, modes, triples


def write_reliability_diagram(report: Any, path: Path) -> bool:
    """The diagram. Returns False if matplotlib is unavailable, rather than raising."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    bins = [row for row in report.reliability if row["count"] > 0]
    predicted = [row["mean_predicted"] for row in bins]
    observed = [row["observed_frequency"] for row in bins]
    counts = [row["count"] for row in bins]

    figure, (top, bottom) = plt.subplots(
        2, 1, figsize=(6, 7), height_ratios=[3, 1], constrained_layout=True
    )
    top.plot([0, 1], [0, 1], "--", color="#999", linewidth=1, label="perfect calibration")
    top.plot(predicted, observed, "o-", color="#1f6feb", linewidth=2, label="observed")
    top.set_xlabel("mean predicted probability")
    top.set_ylabel("observed frequency")
    top.set_title(
        f"Reliability -- out-of-fold, {report.folds}-fold cross-fitted\n"
        f"ECE {report.ece:.4f}   Brier {report.brier:.4f}   "
        f"AUROC {report.auroc:.3f}   n={report.n_items}"
    )
    top.set_xlim(0, 1)
    top.set_ylim(0, 1)
    top.legend(loc="upper left", fontsize=8)
    top.grid(alpha=0.25)

    # The histogram is not decoration. A reliability curve drawn through bins holding
    # three items each looks like a calibration result and is noise; the counts are how
    # a reader tells the difference.
    bottom.bar(predicted, counts, width=0.08, color="#8b949e")
    bottom.set_xlabel("mean predicted probability")
    bottom.set_ylabel("items in bin")
    bottom.set_xlim(0, 1)
    bottom.grid(alpha=0.25)

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=140)
    plt.close(figure)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--items",
        type=int,
        default=10000,
        # At a 10% defect base rate this yields ~1,000 defective items, which is what the
        # conformal bound needs -- a 1% guarantee at 90% confidence is unreachable below
        # roughly 500, whatever the detector's quality. Generation is deterministic and
        # takes seconds, so there is no reason to be stingy here.
        help="how many triples to generate (default sized for the conformal bound)",
    )
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=None, help="defaults to the policy's")
    parser.add_argument("--delta", type=float, default=None, help="defaults to the policy's")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "artifacts" / "calibration")
    parser.add_argument(
        "--with-probe",
        action="store_true",
        help="add the trained observer probe as a feature (slow: ~123 ms/item on CPU)",
    )
    parser.add_argument("--probe-model", default=None)
    args = parser.parse_args()

    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    alpha = args.alpha if args.alpha is not None else policy.guarantees.max_ungrounded_escape_rate
    # Rounded: 1.0 - 0.90 is 0.09999999999999998 in binary floating point, and a
    # guarantee reported to 17 significant figures reads as a bug even when it is not.
    delta = args.delta if args.delta is not None else round(1.0 - policy.guarantees.confidence, 10)

    print(f"building {args.items} labelled triples (seed {args.seed})")
    features, labels, modes, triples = build_dataset(args.items, args.seed)
    print(f"  {len(labels)} items, {int(labels.sum())} defective\n")

    signals = list(GROUNDING_SIGNALS)
    if args.with_probe:
        print("  encoding through the observer probe (this takes a while)...")
        probe_column = _probe_features(triples, args.probe_model)
        if probe_column is not None:
            features = np.hstack([features, probe_column])
            signals = [*signals, "observer.probe"]
            print(f"  added observer.probe; {features.shape[1]} features total\n")

    calibrator = SignalCalibrator(signals=signals, folds=args.folds)

    print(f"cross-fitting {args.folds} folds for the reported metrics")
    probabilities, _ = calibrator.evaluate(features, labels)

    print("fitting the production calibrator on all items")
    calibrator.fit(features, labels)

    # Per-defect, one-vs-rest. The objective needs P(d) per class, not one
    # P(something is wrong): a contradiction and an ungrounded claim carry different
    # impact multipliers and are removed by different actions at different efficacies.
    labels_by_defect = {
        defect: np.array([int(t.defect == defect) for t in triples])
        for defect in sorted({t.defect for t in triples if t.defect})
    }
    per_defect = MultiDefectCalibrator(signals=signals, folds=args.folds)
    per_defect.fit(features, labels_by_defect)
    print(f"  per-defect calibrators: {sorted(per_defect.per_defect)}")
    if per_defect.skipped:
        print(f"  ! not fitted (too few positives): {per_defect.skipped}")

    report = build_report(
        probabilities=probabilities,
        labels=labels,
        features=features,
        signals=signals,
        modes=modes,
        folds=args.folds,
    )

    print(
        f"\n  ECE    {report.ece:.4f}   (target < 0.05: {'PASS' if report.meets_target else 'FAIL'})"
    )
    print(f"  Brier  {report.brier:.4f}")
    print(f"  AUROC  {report.auroc:.4f}")
    print("\n  per-signal AUROC:")
    for name, value in sorted(report.signal_auroc.items(), key=lambda kv: -kv[1]):
        print(f"    {name:38} {value:.3f}")
    print("\n  mean predicted probability by failure mode:")
    for name, value in sorted(report.mode_mean_probability.items(), key=lambda kv: -kv[1]):
        print(f"    {name:38} {value:.3f}")

    # The number F-019 actually turns on. AUROC says how well a detector RANKS; this
    # says how low it can push a genuinely clean sentence, and that is what decides
    # whether the objective will ever let high-stakes traffic pass.
    clean_mask = np.array([m == "clean" for m in modes])
    if clean_mask.any():
        clean_scores = probabilities[clean_mask]
        print(f"{NEWLINE}  CLEAN-TEXT FLOOR (what F-019 turns on, not AUROC):")
        for quantile in (0.25, 0.50, 0.75):
            print(
                f"    {quantile:.0%} of clean text calibrates below "
                f"{float(np.quantile(clean_scores, quantile)):.5f}"
            )
        print("    the objective needs: Rs.3,000 -> below 0.00033, Rs.40,000 -> below 0.000025")

    # ---- the conformal threshold ------------------------------------------ #
    #
    # Certified against the UNGROUNDED per-defect calibrator's out-of-fold scores, not
    # against the binary "is anything wrong" fusion above. The guarantee in the policy is
    # specifically about ungrounded escapes, and the engine gates on P(ungrounded) -- so
    # that is the score the threshold has to be computed on.
    #
    # It was originally computed on the binary fusion, which is a different number on a
    # different scale. Nothing raised. The threshold landed at 0.020 against a binary
    # clean baseline of 0.025 (intervene on everything) and was then applied to a
    # per-defect clean baseline of 0.019 (intervene on nothing), so guaranteed mode was a
    # silent no-op. It was only caught because `make eval --conformal-filter` printed
    # numbers identical to operating mode.
    print(f"\nconformal threshold search (alpha={alpha}, delta={delta})")
    print("  certified against P(ungrounded) -- the score the engine actually gates on")
    ungrounded_labels = labels_by_defect.get("ungrounded")
    if ungrounded_labels is None:
        print("  ! no 'ungrounded' class in the data; the policy's guarantee cannot be certified")
        return 1
    ungrounded_probs, _ = per_defect.evaluate("ungrounded", features, ungrounded_labels)
    result = select_threshold(ungrounded_probs, ungrounded_labels, alpha=alpha, delta=delta)
    print(f"  {result.statement()}")
    if result.certified:
        print(f"  intervention rate at that threshold: {result.intervention_rate:.1%}")

    for note in report.notes + result.notes:
        print(f"  ! {note}")

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    calibrator.save(out / "calibrator.json")
    per_defect.save(out / "calibrator_per_defect.json")
    (out / "report.json").write_text(report.to_json(), encoding="utf-8")
    result.save(out / "lambda.json")
    drawn = write_reliability_diagram(report, out / "reliability.png")
    (out / "dataset.json").write_text(
        json.dumps(
            {
                "items": args.items,
                "seed": args.seed,
                "source": "induced failures from corpus/manifest.json",
                "warning": (
                    "Induced data, not human labels. It calibrates; it does not audit. "
                    "D2-B3's hand-labelled anchor set is what the meta-monitor re-scores."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  wrote {out}/ (calibrator, report, lambda{', reliability.png' if drawn else ''})")
    if not drawn:
        print("  ! matplotlib unavailable -- reliability.png not written")

    # Non-zero when the guarantee could not be made. The alternative is a script that
    # always succeeds and a claim nobody checked.
    return 0 if result.certified else 1


if __name__ == "__main__":
    raise SystemExit(main())
