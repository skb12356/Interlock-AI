"""Train the observer's linear probes and produce the accuracy-by-layer curve.

    uv run python scripts/train_probes.py
    uv run python scripts/train_probes.py --items 3000 --model roberta-base

Takes the labelled triples from ``eval/induce.py``, runs each through the observer
encoder as an NLI-shaped ``(context, answer)`` pair, and fits one logistic probe per
layer on the pooled hidden states. The layer is selected on **held-out** AUROC and the
whole curve is written out — which layer carries the signal is itself the finding.

Two things worth watching in the output.

**The shape of the curve.** Signal rising through the middle of the stack and flattening
is the expected pattern. Signal only at the final layer usually means the probe has
found the encoder's own task head rather than anything about grounding.

**Whether the probe beats the deterministic signals.** The lexical grounding checks reach
AUROC ~0.83 for nothing. A probe that costs a 94 ms forward pass and scores 0.7 has not
earned its place, and the script says so rather than shipping it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

NEWLINE = chr(10)
warnings.filterwarnings("ignore")

from interlock.eval.induce import TripleGenerator  # noqa: E402
from interlock.observer.encoder import DEFAULT_ENCODER, ObserverEncoder  # noqa: E402
from interlock.observer.probes import train_probes  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.signals.grounding import GROUNDING_SIGNALS, grounding_signals  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=2000)
    parser.add_argument("--model", default=DEFAULT_ENCODER)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "artifacts" / "probes")
    args = parser.parse_args()

    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    triples = TripleGenerator(chunks=corpus_chunks(documents), seed=args.seed).generate(args.items)
    labels = np.array([int(t.is_defective) for t in triples])
    print(f"{len(triples)} triples, {int(labels.sum())} defective")

    # NLI shape: the retrieved context is the premise, the answer sentence is the
    # hypothesis. This is the pairing the encoder was fine-tuned on, which is the whole
    # reason for choosing an NLI checkpoint over a plain masked-LM one.
    # Untrusted passages are excluded, and this MUST match how the probe is SERVED
    # (interlock/signals/probe_signal.py and scripts/calibrate.py both exclude them).
    #
    # It did not, at first, and the cost was measurable: a probe trained on all context
    # scored AUROC 0.907 on a fresh seed of its own distribution but only 0.837 when
    # served without the untrusted passages it had learned to expect. Classic
    # train/serve skew -- nothing errors, the number is just quietly worse, and it would
    # have been read as "the probe is weak" rather than as a mismatch.
    #
    # Serving is the side that cannot move: a poisoned document in the premise makes the
    # attacker's own claim genuinely entailed, so the probe would faithfully report
    # "supported". Training matches serving.
    premises = [
        NEWLINE.join(f.text for f in t.context if not str(f.provenance).endswith("untrusted"))
        or "(no context retrieved)"
        for t in triples
    ]
    hypotheses = [t.answer for t in triples]

    encoder = ObserverEncoder(model_name=args.model)
    started = time.time()
    encoder.load()
    print(
        f"  loaded {encoder.model_name}: {encoder.n_layers} layers, {encoder.hidden_size} dim "
        f"({time.time() - started:.1f}s)"
    )

    started = time.time()
    batch = encoder.encode(premises, hypotheses, batch_size=args.batch_size)
    elapsed = time.time() - started
    print(f"  encoded in {elapsed:.1f}s ({elapsed / len(triples) * 1000:.0f} ms/pair)\n")

    bundle = train_probes(batch.layers, labels, model_name=encoder.model_name, seed=args.seed)

    print("accuracy by layer (held-out AUROC):")
    for row in bundle.curve:
        bar = "#" * int(max(0.0, row.auroc - 0.5) * 100)
        marker = "  <- selected" if row.layer == bundle.best_layer else ""
        print(
            f"  layer {row.layer:2}  {row.auroc:.4f}  (train {row.train_auroc:.4f})  {bar}{marker}"
        )

    # The comparison that decides whether this earns its 94 ms.
    features = np.array(
        [
            list(grounding_signals(t.answer, t.context, question=t.question).as_features().values())
            for t in triples
        ]
    )
    from sklearn.metrics import roc_auc_score

    best_deterministic = max(
        (float(roc_auc_score(labels, features[:, i])), name)
        for i, name in enumerate(GROUNDING_SIGNALS)
    )
    print(
        f"\n  best deterministic signal: {best_deterministic[1]} at AUROC "
        f"{best_deterministic[0]:.4f} (free)"
    )
    print(
        f"  probe:                     layer {bundle.best_layer} at AUROC "
        f"{bundle.best_auroc:.4f} ({elapsed / len(triples) * 1000:.0f} ms/pair)"
    )

    verdict: str
    if bundle.best_auroc > best_deterministic[0] + 0.03:
        verdict = "the probe earns its forward pass"
        print(f"\n  {verdict.upper()}: +{bundle.best_auroc - best_deterministic[0]:.3f} AUROC.")
    else:
        verdict = "the probe does NOT earn its forward pass"
        print(
            f"\n  {verdict.upper()}. It costs "
            f"{elapsed / len(triples) * 1000:.0f} ms per sentence and adds "
            f"{bundle.best_auroc - best_deterministic[0]:+.3f} AUROC over a free lexical "
            f"check. Ship it only as one signal among several, never as a replacement."
        )

    for note in bundle.notes:
        print(f"  ! {note}")

    args.out.mkdir(parents=True, exist_ok=True)
    bundle.save(args.out / "probe.json")
    (args.out / "curve.json").write_text(
        json.dumps(
            {
                "model": encoder.model_name,
                "n_items": len(triples),
                "n_defective": int(labels.sum()),
                "ms_per_pair": round(elapsed / len(triples) * 1000, 1),
                "best_layer": bundle.best_layer,
                "best_auroc": bundle.best_auroc,
                "best_deterministic_auroc": best_deterministic[0],
                "best_deterministic_signal": best_deterministic[1],
                "verdict": verdict,
                "curve": [
                    {"layer": r.layer, "auroc": r.auroc, "train_auroc": r.train_auroc}
                    for r in bundle.curve
                ],
                "notes": bundle.notes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  wrote {args.out}/probe.json and curve.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
