"""Linear probes over the observer's residual stream.

The published result this re-implements: a *linear* classifier on a language model's
hidden states detects likely hallucination from a single forward pass. Linear matters —
it is what keeps the check cheap enough to run per sentence, and it is what makes the
claim interesting. A large non-linear head on top of an encoder is just a second model,
and would need its own calibration, its own drift monitoring and its own defence.

**One probe per layer, then pick by held-out AUROC.** Which layer carries the signal is
not knowable in advance and is not the same across models, so all of them are fitted and
the accuracy-by-layer curve is the artefact. That curve is also a diagnostic: signal
concentrated in the middle layers is the expected shape, and signal only at the last
layer usually means the probe has learned the encoder's task head rather than anything
about grounding.

**Selected on held-out data, never on the training fold.** With 768 features and a few
thousand examples, a probe can fit training noise comfortably. Picking the best layer by
training AUROC would reliably choose the layer that overfits hardest, and report its
training score as evidence.

**The output is a score, not a probability.** It goes through the same isotonic
calibration as every other signal (ADR-002) before anything prices with it. A probe's
raw ``predict_proba`` is *not* calibrated, whatever the method is called.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

__all__ = ["LayerResult", "ProbeBundle", "ProbeTrainer", "train_probes"]

#: Held-out share for layer selection. Generous because selection is the thing most
#: likely to fool us here, and a thin held-out split makes the choice noisy.
HELD_OUT = 0.3

#: L2 strength. Deliberately strong: hidden states are high-dimensional and correlated,
#: and an unregularised probe on 768 features will happily memorise a few thousand rows.
DEFAULT_C = 0.05


@dataclass(frozen=True, slots=True)
class LayerResult:
    """One layer's probe and how well it did on data it never saw."""

    layer: int
    auroc: float
    train_auroc: float
    n_train: int
    n_test: int

    @property
    def overfit_gap(self) -> float:
        """Train minus held-out. A large gap means the probe memorised."""
        return self.train_auroc - self.auroc


@dataclass
class ProbeBundle:
    """The selected probe, the curve behind it, and enough to reproduce the choice."""

    model_name: str
    best_layer: int
    best_auroc: float
    curve: list[LayerResult] = field(default_factory=list)
    #: Serialised probe: standardiser + logistic weights. Plain data, not pickle --
    #: these ship in the evidence pack and a reviewer must be able to read them.
    scaler_mean: list[float] = field(default_factory=list)
    scaler_scale: list[float] = field(default_factory=list)
    coefficients: list[float] = field(default_factory=list)
    intercept: float = 0.0
    notes: list[str] = field(default_factory=list)

    def score(self, hidden: np.ndarray) -> np.ndarray:
        """Raw probe scores in [0, 1]. **Not calibrated** -- see the module docstring."""
        standardised = (np.asarray(hidden, dtype=float) - np.asarray(self.scaler_mean)) / np.asarray(
            self.scaler_scale
        )
        logits = standardised @ np.asarray(self.coefficients) + self.intercept
        return 1.0 / (1.0 + np.exp(-logits))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["curve"] = [asdict(item) for item in self.curve]
        return payload

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> ProbeBundle:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        curve = [LayerResult(**item) for item in payload.pop("curve", [])]
        return cls(curve=curve, **payload)


@dataclass
class ProbeTrainer:
    """Fits one probe per layer and selects on held-out AUROC."""

    held_out: float = HELD_OUT
    regularisation: float = DEFAULT_C
    seed: int = 20260826

    def fit(
        self, layers: list[np.ndarray], labels: np.ndarray, *, model_name: str = ""
    ) -> ProbeBundle:
        labels = np.asarray(labels, dtype=int)
        if len(np.unique(labels)) < 2:
            raise ValueError("probes need both classes present")

        # One split, shared by every layer. Fitting each layer on a different split would
        # make the AUROCs incomparable, and the whole point is to compare them.
        indices = np.arange(len(labels))
        train_idx, test_idx = train_test_split(
            indices, test_size=self.held_out, random_state=self.seed, stratify=labels
        )

        curve: list[LayerResult] = []
        fitted: dict[int, tuple[StandardScaler, LogisticRegression]] = {}

        for index, hidden in enumerate(layers):
            hidden = np.asarray(hidden, dtype=float)
            scaler = StandardScaler().fit(hidden[train_idx])
            probe = LogisticRegression(
                C=self.regularisation, max_iter=2000, class_weight="balanced"
            )
            probe.fit(scaler.transform(hidden[train_idx]), labels[train_idx])

            test_scores = probe.decision_function(scaler.transform(hidden[test_idx]))
            train_scores = probe.decision_function(scaler.transform(hidden[train_idx]))
            curve.append(
                LayerResult(
                    layer=index,
                    auroc=float(roc_auc_score(labels[test_idx], test_scores)),
                    train_auroc=float(roc_auc_score(labels[train_idx], train_scores)),
                    n_train=len(train_idx),
                    n_test=len(test_idx),
                )
            )
            fitted[index] = (scaler, probe)

        best = max(curve, key=lambda item: item.auroc)
        scaler, probe = fitted[best.layer]

        notes: list[str] = []
        if best.overfit_gap > 0.15:
            notes.append(
                f"layer {best.layer} has a train/held-out AUROC gap of "
                f"{best.overfit_gap:.3f} -- the probe is partly memorising, and more "
                f"regularisation or more data would help"
            )
        if best.layer == len(layers) - 1:
            notes.append(
                "the best layer is the LAST one, which often means the probe has learned "
                "the encoder's own task head rather than anything about grounding; "
                "signal concentrated mid-stack is the expected shape"
            )
        if best.auroc < 0.65:
            notes.append(
                f"best held-out AUROC is only {best.auroc:.3f} -- this probe is barely "
                f"better than the deterministic signals and should not displace them"
            )

        return ProbeBundle(
            model_name=model_name,
            best_layer=best.layer,
            best_auroc=best.auroc,
            curve=curve,
            scaler_mean=[float(v) for v in scaler.mean_],
            scaler_scale=[float(v) for v in scaler.scale_],
            coefficients=[float(v) for v in probe.coef_[0]],
            intercept=float(probe.intercept_[0]),
            notes=notes,
        )


def train_probes(
    layers: list[np.ndarray], labels: np.ndarray, *, model_name: str = "", seed: int = 20260826
) -> ProbeBundle:
    return ProbeTrainer(seed=seed).fit(layers, labels, model_name=model_name)
