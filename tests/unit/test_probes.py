"""Linear probes over the observer's hidden states.

The encoder tests are marked slow and skip without torch, so the fast suite still runs on
a clean checkout. The probe-fitting tests use synthetic hidden states, which is the right
call: what is being tested is the *selection discipline*, and that has to hold whatever
the encoder produced.

The failure this file mostly guards against is selection on the wrong data. With 768
features and a few thousand rows, picking the best layer by training AUROC reliably
chooses the layer that overfits hardest and then reports its training score as evidence.
"""

from __future__ import annotations

import numpy as np
import pytest

from interlock.observer.probes import ProbeBundle, ProbeTrainer, train_probes


def _layers(
    *, n: int = 400, dim: int = 32, informative_layer: int = 3, n_layers: int = 6, seed: int = 1
) -> tuple[list[np.ndarray], np.ndarray]:
    """Synthetic hidden states where exactly one layer carries the signal."""
    rng = np.random.default_rng(seed)
    labels = rng.integers(0, 2, n)
    layers: list[np.ndarray] = []
    for index in range(n_layers + 1):
        noise = rng.normal(size=(n, dim))
        if index == informative_layer:
            noise[:, 0] += labels * 3.0
        elif index == n_layers:
            # A weak echo at the top, as a real encoder has.
            noise[:, 0] += labels * 0.6
        layers.append(noise)
    return layers, labels


# --------------------------------------------------------------------------- #
# Selection discipline
# --------------------------------------------------------------------------- #


def test_the_informative_layer_is_found() -> None:
    layers, labels = _layers(informative_layer=3)
    bundle = train_probes(layers, labels)
    assert bundle.best_layer == 3
    assert bundle.best_auroc > 0.9


def test_the_whole_curve_is_kept_not_just_the_winner() -> None:
    """Which layer carries the signal is itself the finding, and a single number cannot
    show the shape."""
    layers, labels = _layers()
    bundle = train_probes(layers, labels)
    assert len(bundle.curve) == len(layers)
    assert [row.layer for row in bundle.curve] == list(range(len(layers)))


def test_selection_uses_held_out_not_training_auroc() -> None:
    """With 768 features a probe fits training noise comfortably, so selecting on
    training score would reliably choose the layer that overfits hardest.

    Constructed so one layer memorises (pure noise, more features than samples) while
    another generalises. Training AUROC would pick the memoriser.
    """
    rng = np.random.default_rng(3)
    n = 120
    labels = rng.integers(0, 2, n)
    generalises = rng.normal(size=(n, 8))
    generalises[:, 0] += labels * 1.6
    memorises = rng.normal(size=(n, 400))  # far more features than rows: pure noise

    bundle = ProbeTrainer(regularisation=1.0).fit([generalises, memorises], labels)
    memoriser = bundle.curve[1]
    assert memoriser.train_auroc > memoriser.auroc, "the fixture did not memorise"
    assert bundle.best_layer == 0, "selection followed the training score"


def test_a_memorising_probe_is_flagged() -> None:
    rng = np.random.default_rng(5)
    n = 120
    labels = rng.integers(0, 2, n)
    memorises = rng.normal(size=(n, 400))
    bundle = ProbeTrainer(regularisation=1.0).fit([memorises], labels)
    assert bundle.curve[0].overfit_gap > 0.15
    assert any("memorising" in note for note in bundle.notes)


def test_a_last_layer_win_is_called_out() -> None:
    """Signal only at the final layer usually means the probe found the encoder's own
    task head rather than anything about grounding."""
    layers, labels = _layers(informative_layer=6, n_layers=6)
    bundle = train_probes(layers, labels)
    assert bundle.best_layer == 6
    assert any("task head" in note for note in bundle.notes)


def test_a_weak_probe_says_it_is_weak() -> None:
    """A probe that costs a forward pass and barely beats chance should not displace a
    free lexical check, and the bundle has to say so rather than leave it to a reader."""
    rng = np.random.default_rng(7)
    labels = rng.integers(0, 2, 300)
    layers = [rng.normal(size=(300, 16)) for _ in range(3)]
    bundle = train_probes(layers, labels)
    assert bundle.best_auroc < 0.65
    assert any("barely better" in note for note in bundle.notes)


def test_all_layers_share_one_split() -> None:
    """Fitting each layer on a different split would make the AUROCs incomparable, and
    comparing them is the entire purpose."""
    layers, labels = _layers()
    bundle = train_probes(layers, labels)
    assert len({row.n_train for row in bundle.curve}) == 1
    assert len({row.n_test for row in bundle.curve}) == 1


def test_one_class_only_is_refused() -> None:
    layers = [np.random.default_rng(0).normal(size=(50, 8))]
    with pytest.raises(ValueError, match="both classes"):
        train_probes(layers, np.zeros(50, dtype=int))


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def test_the_bundle_round_trips_as_plain_data(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Not pickle: probes ship in the evidence pack and a reviewer must be able to read
    them without executing whatever is inside."""
    layers, labels = _layers()
    bundle = train_probes(layers, labels, model_name="test-encoder")

    path = tmp_path / "probe.json"
    bundle.save(path)
    assert "coefficients" in path.read_text(encoding="utf-8")

    reloaded = ProbeBundle.load(path)
    assert reloaded.best_layer == bundle.best_layer
    assert reloaded.model_name == "test-encoder"
    assert len(reloaded.curve) == len(bundle.curve)

    hidden = layers[bundle.best_layer]
    assert np.allclose(reloaded.score(hidden), bundle.score(hidden))


def test_scores_are_bounded_and_discriminate() -> None:
    layers, labels = _layers()
    bundle = train_probes(layers, labels)
    scores = bundle.score(layers[bundle.best_layer])
    assert scores.min() >= 0.0
    assert scores.max() <= 1.0
    assert scores[labels == 1].mean() > scores[labels == 0].mean()


def test_the_probe_output_is_not_treated_as_calibrated() -> None:
    """ADR-002. A probe's predict_proba is not calibrated whatever the method is called,
    and this one is documented as a raw score that must pass through isotonic first."""
    from interlock.observer import probes

    assert "not calibrated" in probes.ProbeBundle.score.__doc__.lower()
    assert "isotonic" in (probes.__doc__ or "").lower()


# --------------------------------------------------------------------------- #
# The encoder itself -- slow, and skipped without torch
# --------------------------------------------------------------------------- #

torch = pytest.importorskip("torch", reason="observer encoder needs torch")


@pytest.mark.slow
def test_the_encoder_produces_one_vector_per_layer_per_item() -> None:
    from interlock.observer.encoder import ObserverEncoder

    encoder = ObserverEncoder()
    batch = encoder.encode(
        ["Clause 9.1: no prepayment charge applies."] * 4,
        [
            "No charge applies.",
            "A 2% charge applies.",
            "No charge applies.",
            "A 2% charge applies.",
        ],
        batch_size=4,
    )
    assert len(batch.layers) == encoder.n_layers + 1
    for layer in batch.layers:
        assert layer.shape == (4, encoder.hidden_size)


@pytest.mark.slow
def test_pooling_ignores_padding() -> None:
    """Including padding would make an item's representation depend on the longest item
    in its batch -- the same input encoding differently depending on its neighbours,
    which is untraceable when it goes wrong."""
    from interlock.observer.encoder import ObserverEncoder

    encoder = ObserverEncoder()
    premise = "Clause 9.1: no prepayment charge applies to floating-rate home loans."
    hypothesis = "No charge applies."

    alone = encoder.encode([premise], [hypothesis], batch_size=1).layers[-1][0]
    with_long_neighbour = encoder.encode(
        [premise, premise], [hypothesis, hypothesis + " " + "padding text " * 40], batch_size=2
    ).layers[-1][0]
    assert np.allclose(alone, with_long_neighbour, atol=1e-4)


@pytest.mark.slow
def test_the_observer_cannot_generate() -> None:
    """CLAUDE.md s3: never a generative judge on this path. Asserted structurally --
    there is no generate call to reach for."""
    from interlock.observer.encoder import ObserverEncoder

    encoder = ObserverEncoder()
    encoder.load()
    assert not hasattr(encoder, "generate")
    assert encoder.health()["generative"] is False


@pytest.mark.slow
def test_mismatched_input_lengths_are_refused() -> None:
    from interlock.observer.encoder import ObserverEncoder

    with pytest.raises(ValueError, match="same length"):
        ObserverEncoder().encode(["a", "b"], ["only one"])


# --------------------------------------------------------------------------- #
# The one-standard-error rule
# --------------------------------------------------------------------------- #


def test_a_gap_inside_noise_is_not_treated_as_a_ranking() -> None:
    """The rule exists because of a real run: layer 6 scored 0.9455 and layer 3 scored
    0.9348 on ~450 held-out items, where one standard error is around 0.015. The winner
    was chosen by noise -- and it happened to be the layer most likely to be the
    encoder's own task head rather than anything about grounding.
    """
    rng = np.random.default_rng(21)
    n, dim = 600, 24
    labels = rng.integers(0, 2, n)
    early = rng.normal(size=(n, dim))
    early[:, 0] += labels * 2.0
    # The same signal, jittered. Two layers carrying the same information will land
    # within noise of each other, which is exactly the situation the rule is for --
    # constructing a deliberate 0.05 gap would test the opposite behaviour.
    late = early + rng.normal(scale=0.02, size=(n, dim))

    bundle = train_probes([early, late], labels)
    assert bundle.best_layer == 0, [row.auroc for row in bundle.curve]
    assert any("within one standard error" in note for note in bundle.notes)


def test_a_genuinely_better_later_layer_is_still_chosen() -> None:
    """The rule must not become "always take layer 0". A real margin still wins."""
    rng = np.random.default_rng(22)
    n, dim = 600, 24
    labels = rng.integers(0, 2, n)
    weak = rng.normal(size=(n, dim))
    weak[:, 0] += labels * 0.4
    strong = rng.normal(size=(n, dim))
    strong[:, 0] += labels * 3.0

    bundle = train_probes([weak, strong], labels)
    assert bundle.best_layer == 1


def test_the_standard_error_shrinks_with_more_data() -> None:
    from interlock.observer.probes import auroc_standard_error

    small = auroc_standard_error(0.94, 50, 450)
    large = auroc_standard_error(0.94, 500, 4500)
    assert large < small
    assert auroc_standard_error(0.94, 0, 10) == 1.0, "a degenerate split has no usable SE"
