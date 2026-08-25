"""Turning scores into probabilities, and proving they are probabilities.

CLAUDE.md s3: *scores are not probabilities until calibrated*. Everything downstream of
this file does arithmetic in rupees -- surviving harm is ``P(defect) x Impact`` -- and
that arithmetic is only meaningful if ``P`` means what it says. A detector that outputs
0.8 on things that are wrong 30% of the time will produce an expected-loss table that is
precise, auditable, and off by a factor of three.

Three things happen here.

**Isotonic regression per signal.** Monotone, non-parametric, and it cannot invent a
shape the data does not show. Platt scaling would impose a sigmoid on scores that are
often not sigmoid-shaped at all -- ``numeric_unsupported`` is nearly a step function --
and the fitted parameters would smooth away exactly the sharp threshold that makes it
useful.

**Cross-fitting, 5-fold.** A calibrator evaluated on the data it was fitted to reports
an ECE near zero no matter how bad it is; isotonic regression in particular can drive
in-sample error to nothing by memorising. So every item's calibrated probability comes
from a model that never saw it. This is not a nicety -- an uncross-fitted ECE is the
single easiest number in this project to accidentally lie with.

**A held-out report.** ECE, Brier, AUROC and a reliability diagram, computed on
out-of-fold predictions and written to ``artifacts/``. The plan's target is ECE < 0.05.

Fusion is a logistic model over the *calibrated* signals, cross-fitted on the same
folds. Fitting the fusion on in-fold calibrated values would leak the label through the
calibrator into the fusion, which is the classic way a stacked model looks excellent
until it meets traffic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold

__all__ = [
    "CalibrationReport",
    "MultiDefectCalibrator",
    "SignalCalibrator",
    "expected_calibration_error",
    "reliability_curve",
]

#: 10 equal-width bins over [0, 1]. Equal-width rather than equal-mass because the
#: claim being made is about the probability axis: "when we say 0.7, it happens 70% of
#: the time" is a statement about the interval [0.65, 0.75], not about a quantile.
DEFAULT_BINS = 10

#: The plan's target. Reported against, never silently enforced.
ECE_TARGET = 0.05


def expected_calibration_error(
    probabilities: np.ndarray, labels: np.ndarray, *, bins: int = DEFAULT_BINS
) -> float:
    """Weighted mean gap between predicted confidence and observed frequency.

    Empty bins contribute nothing rather than zero: a bin with no items has no
    observed frequency, and averaging a fictional 0 gap into the result makes a
    detector that only ever predicts 0.2 look beautifully calibrated everywhere.
    """
    probabilities = np.asarray(probabilities, dtype=float)
    labels = np.asarray(labels, dtype=float)
    if probabilities.size == 0:
        return 0.0

    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for lower, upper in pairwise(edges):
        mask = (probabilities > lower) & (probabilities <= upper)
        if lower == 0.0:
            mask |= probabilities == 0.0
        count = int(mask.sum())
        if count == 0:
            continue
        gap = abs(float(probabilities[mask].mean()) - float(labels[mask].mean()))
        total += (count / probabilities.size) * gap
    return total


def reliability_curve(
    probabilities: np.ndarray, labels: np.ndarray, *, bins: int = DEFAULT_BINS
) -> list[dict[str, float]]:
    """Per-bin (mean predicted, observed frequency, count) for the diagram."""
    probabilities = np.asarray(probabilities, dtype=float)
    labels = np.asarray(labels, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: list[dict[str, float]] = []
    for lower, upper in pairwise(edges):
        mask = (probabilities > lower) & (probabilities <= upper)
        if lower == 0.0:
            mask |= probabilities == 0.0
        count = int(mask.sum())
        out.append(
            {
                "bin_lower": float(lower),
                "bin_upper": float(upper),
                "count": count,
                "mean_predicted": float(probabilities[mask].mean()) if count else 0.0,
                "observed_frequency": float(labels[mask].mean()) if count else 0.0,
            }
        )
    return out


@dataclass
class CalibrationReport:
    """What the calibration run measured. Written to disk beside the model."""

    n_items: int
    n_positive: int
    folds: int
    signals: list[str]
    #: Out-of-fold metrics for the fused probability.
    ece: float
    brier: float
    auroc: float
    #: Per-signal out-of-fold AUROC, so a signal that contributes nothing is visible.
    signal_auroc: dict[str, float] = field(default_factory=dict)
    #: Per-failure-mode mean fused probability. This is where the honest limits show:
    #: a mode the signals cannot see has a mean barely above the clean class.
    mode_mean_probability: dict[str, float] = field(default_factory=dict)
    reliability: list[dict[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def meets_target(self) -> bool:
        return self.ece < ECE_TARGET

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class SignalCalibrator:
    """Per-signal isotonic maps plus a logistic fusion over them."""

    signals: list[str]
    bins: int = DEFAULT_BINS
    folds: int = 5
    _isotonic: dict[str, IsotonicRegression] = field(default_factory=dict, init=False)
    _fusion: LogisticRegression | None = field(default=None, init=False)
    fitted: bool = field(default=False, init=False)

    # -- fitting ---------------------------------------------------------- #

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        """Fit the production model on everything. Metrics come from :meth:`evaluate`.

        Deliberately separate. The model that ships should use all the data; the
        numbers that get reported must come from data the model never saw. Doing both
        in one pass is how an in-sample ECE ends up on a slide.
        """
        features = np.asarray(features, dtype=float)
        labels = np.asarray(labels, dtype=int)
        calibrated = np.zeros_like(features)
        for index, name in enumerate(self.signals):
            model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            model.fit(features[:, index], labels)
            self._isotonic[name] = model
            calibrated[:, index] = model.predict(features[:, index])

        self._fusion = _fit_fusion(calibrated, labels)
        self.fitted = True

    def evaluate(self, features: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Cross-fitted out-of-fold probabilities. Returns ``(probabilities, labels)``.

        Every item is scored by a calibrator and a fusion that were fitted without it.
        """
        features = np.asarray(features, dtype=float)
        labels = np.asarray(labels, dtype=int)
        out = np.zeros(len(labels), dtype=float)

        splitter = StratifiedKFold(n_splits=self.folds, shuffle=True, random_state=20260825)
        for train_idx, test_idx in splitter.split(features, labels):
            fold_calibrated_train = np.zeros((len(train_idx), len(self.signals)))
            fold_calibrated_test = np.zeros((len(test_idx), len(self.signals)))
            for index in range(len(self.signals)):
                model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                model.fit(features[train_idx, index], labels[train_idx])
                fold_calibrated_train[:, index] = model.predict(features[train_idx, index])
                fold_calibrated_test[:, index] = model.predict(features[test_idx, index])

            fusion = _fit_fusion(fold_calibrated_train, labels[train_idx])
            out[test_idx] = _fusion_predict(fusion, fold_calibrated_test)
        return out, labels

    # -- inference -------------------------------------------------------- #

    def calibrate_one(self, name: str, raw: float) -> float:
        """One signal's calibrated probability. Uncalibrated signals pass through."""
        model = self._isotonic.get(name)
        if model is None:
            return float(np.clip(raw, 0.0, 1.0))
        return float(np.clip(model.predict([raw])[0], 0.0, 1.0))

    def predict(self, features: dict[str, float]) -> float:
        """The fused probability for one item."""
        if not self.fitted or self._fusion is None:
            raise RuntimeError("calibrator is not fitted")
        row = np.array(
            [[self.calibrate_one(name, features.get(name, 0.0)) for name in self.signals]]
        )
        return float(_fusion_predict(self._fusion, row)[0])

    # -- persistence ------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """Serialise as plain data.

        Pickle would be shorter and is refused: these artefacts are read by the console
        and shipped in the evidence pack, and a pickle is both unreadable to a reviewer
        and executable by whoever loads it.
        """
        if not self.fitted or self._fusion is None:
            raise RuntimeError("calibrator is not fitted")
        return {
            "version": 1,
            "signals": list(self.signals),
            "isotonic": {
                name: {
                    "x": [float(v) for v in model.X_thresholds_],
                    "y": [float(v) for v in model.y_thresholds_],
                }
                for name, model in self._isotonic.items()
            },
            "fusion": {
                "coefficients": [float(v) for v in self._fusion.coef_[0]],
                "intercept": float(self._fusion.intercept_[0]),
            },
        }

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _fit_fusion(calibrated: np.ndarray, labels: np.ndarray) -> LogisticRegression:
    """Logistic fusion over calibrated signals.

    A single-class fold would raise; that cannot happen under StratifiedKFold, but the
    production ``fit`` has no such guarantee if a corpus ever produced one label.
    """
    model = LogisticRegression(max_iter=1000, C=1.0)
    if len(np.unique(labels)) < 2:
        raise ValueError("fusion needs both classes present")
    model.fit(calibrated, labels)
    return model


def _fusion_predict(model: LogisticRegression, calibrated: np.ndarray) -> np.ndarray:
    return np.asarray(model.predict_proba(calibrated)[:, 1], dtype=float)


def build_report(
    *,
    probabilities: np.ndarray,
    labels: np.ndarray,
    features: np.ndarray,
    signals: list[str],
    modes: list[str],
    folds: int,
    bins: int = DEFAULT_BINS,
) -> CalibrationReport:
    """Assemble the held-out report, including the parts that are unflattering."""
    labels = np.asarray(labels, dtype=int)
    notes: list[str] = []

    signal_auroc: dict[str, float] = {}
    for index, name in enumerate(signals):
        column = np.asarray(features, dtype=float)[:, index]
        signal_auroc[name] = (
            float(roc_auc_score(labels, column)) if len(np.unique(column)) > 1 else 0.5
        )

    mode_means: dict[str, float] = {}
    for mode in sorted(set(modes)):
        mask = np.array([m == mode for m in modes])
        mode_means[mode] = float(probabilities[mask].mean()) if mask.any() else 0.0

    clean = mode_means.get("clean", 0.0)
    for mode, mean in mode_means.items():
        if mode != "clean" and mean - clean < 0.05:
            notes.append(
                f"'{mode}' scores {mean:.3f} against a clean baseline of {clean:.3f} -- "
                f"these signals cannot see this failure mode; it needs the observer probe"
            )

    ece = expected_calibration_error(probabilities, labels, bins=bins)
    if ece >= ECE_TARGET:
        notes.append(f"ECE {ece:.4f} is above the plan's target of {ECE_TARGET}")

    return CalibrationReport(
        n_items=len(labels),
        n_positive=int(labels.sum()),
        folds=folds,
        signals=list(signals),
        ece=ece,
        brier=float(brier_score_loss(labels, probabilities)),
        auroc=float(roc_auc_score(labels, probabilities)),
        signal_auroc=signal_auroc,
        mode_mean_probability=mode_means,
        reliability=reliability_curve(probabilities, labels, bins=bins),
        notes=notes,
    )


@dataclass
class MultiDefectCalibrator:
    """One :class:`SignalCalibrator` per defect class, fitted one-vs-rest.

    The objective needs ``P(d)`` **per defect**, not one P(anything is wrong): a
    contradiction and an ungrounded claim carry different impact multipliers and are
    removed by different actions at different efficacies. Collapsing them into a single
    probability and splitting it afterwards by which signal fired hardest would be a
    heuristic wearing calibration's clothes.

    One-vs-rest keeps each map honest about its own class. A defect with few examples
    gets a calibrator that says so -- flat, near the base rate -- rather than borrowing
    confidence from a sibling class it has nothing to do with.
    """

    signals: list[str]
    folds: int = 5
    per_defect: dict[str, SignalCalibrator] = field(default_factory=dict, init=False)
    #: Classes that had too few positives to fit. Recorded, not silently dropped.
    skipped: dict[str, str] = field(default_factory=dict, init=False)

    #: Below this many positives a one-vs-rest fit is noise. The calibrator is not
    #: fitted at all rather than fitted badly, and the class falls back to a flat base
    #: rate, which is at least honest about knowing nothing.
    min_positives: int = 30

    def fit(self, features: np.ndarray, labels_by_defect: dict[str, np.ndarray]) -> None:
        for defect, labels in labels_by_defect.items():
            positives = int(np.asarray(labels).sum())
            if positives < self.min_positives:
                self.skipped[defect] = f"only {positives} positives (need {self.min_positives})"
                continue
            calibrator = SignalCalibrator(signals=list(self.signals), folds=self.folds)
            calibrator.fit(features, labels)
            self.per_defect[defect] = calibrator

    def predict(self, features: dict[str, float]) -> dict[str, float]:
        """``{defect: probability}`` for every class that was fitted."""
        return {
            defect: calibrator.predict(features)
            for defect, calibrator in self.per_defect.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "kind": "multi_defect",
            "signals": list(self.signals),
            "per_defect": {d: c.to_dict() for d, c in self.per_defect.items()},
            "skipped": dict(self.skipped),
        }

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> MultiDefectCalibrator:
        """Rebuild from the saved JSON.

        The isotonic maps are reconstructed as interpolation over their stored
        thresholds rather than by re-fitting -- what ships must be exactly what was
        measured, not something re-derived from data that may have moved since.
        """
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("kind") != "multi_defect":
            raise ValueError(f"{path} is not a multi-defect calibrator")
        instance = cls(signals=list(payload["signals"]))
        for defect, blob in payload.get("per_defect", {}).items():
            instance.per_defect[defect] = _calibrator_from_dict(blob)
        instance.skipped = dict(payload.get("skipped", {}))
        return instance


def _calibrator_from_dict(payload: dict[str, Any]) -> SignalCalibrator:
    """Reconstruct a fitted SignalCalibrator from saved thresholds and weights."""
    calibrator = SignalCalibrator(signals=list(payload["signals"]))
    for name, curve in payload["isotonic"].items():
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        # sklearn's isotonic predict() only needs the fitted step function, which is
        # exactly what was stored. Re-fitting on the knots reproduces it.
        model.fit(np.asarray(curve["x"], dtype=float), np.asarray(curve["y"], dtype=float))
        calibrator._isotonic[name] = model

    fusion = LogisticRegression(max_iter=1000)
    fusion.coef_ = np.array([payload["fusion"]["coefficients"]], dtype=float)
    fusion.intercept_ = np.array([payload["fusion"]["intercept"]], dtype=float)
    fusion.classes_ = np.array([0, 1])
    calibrator._fusion = fusion
    calibrator.fitted = True
    return calibrator
