"""The observer model: a small encoder that reads, and never writes.

ADR-001 and invariant 7. The observer sits *beside* the generator, not inside it, and
that is the whole moat: hallucination probing runs on the observer's own residual stream
over ``(context, question, candidate answer)``, so it needs no access to the generator's
internals and works unchanged against GPT, Claude, Gemini or a local model. A design that
required logprobs from the generator would be a design that only worked on models you
control.

**What it is not.** The observer never generates. It produces hidden states, which a
linear probe reads. A generative judge on this path would be slow, expensive and
overconfident — CLAUDE.md §3 forbids it, and this file has no ``generate`` call to
accidentally reach for.

**Why an NLI-trained encoder.** The question a probe is asked — *is this answer supported
by this context?* — is textual entailment wearing different clothes. An encoder already
fine-tuned on NLI arrives with features that separate entailment from contradiction, and
a linear probe on top has correspondingly little work to do. A plain masked-LM encoder
works and needs a lot more data to reach the same place.

**Deviation D-012.** The plan names ``DeBERTa-v3-base``. transformers 5.x cannot load its
tokenizer: it misroutes the SentencePiece model to the tiktoken parser, which then fails
on the binary file. Installing ``sentencepiece`` and ``tiktoken`` does not help, because
the routing decision happens before either is consulted. Rather than pin an old
transformers across the whole project, the encoder is configurable and defaults to an NLI
cross-encoder whose tokenizer loads natively. The probe pipeline is identical either way.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DEFAULT_ENCODER", "EncodedBatch", "ObserverEncoder"]

#: An NLI cross-encoder: already trained on entailment, which is the question a
#: grounding probe is really asking. Configurable via INTERLOCK_OBSERVER_MODEL.
DEFAULT_ENCODER = "cross-encoder/nli-distilroberta-base"

#: Context can be long and the answer is one sentence. 384 keeps a CPU forward pass in
#: the tens of milliseconds while still fitting a retrieved passage plus a claim.
MAX_TOKENS = 384


@dataclass(frozen=True, slots=True)
class EncodedBatch:
    """Per-layer pooled representations for a batch of (premise, hypothesis) pairs.

    ``layers[i]`` has shape ``(batch, hidden)``: one vector per item per layer. Probes
    are fitted per layer, so the layer axis is kept rather than collapsed -- which layer
    carries the signal is itself a finding (the accuracy-by-layer curve), and averaging
    across layers would destroy exactly that.
    """

    layers: list[Any]
    n_layers: int
    hidden_size: int
    model_name: str

    def layer(self, index: int) -> Any:
        return self.layers[index]


@dataclass
class ObserverEncoder:
    """Loads lazily, runs under ``inference_mode``, and is thread-safe by lock.

    Lazy because importing torch costs a second and a half and the gateway must start
    without it. Locked because a single encoder instance is shared across concurrent
    requests and a transformers model is not re-entrant -- two threads calling forward on
    one module is a class of bug that shows up as nonsense scores rather than a crash.
    """

    model_name: str = DEFAULT_ENCODER
    max_tokens: int = MAX_TOKENS
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _model: Any = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _n_layers: int = field(default=0, init=False)
    _hidden: int = field(default=0, init=False)

    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """Load the weights. Safe to call repeatedly."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:  # another thread won the race
                return
            import torch
            from transformers import AutoModel, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModel.from_pretrained(self.model_name)
            model.eval()
            # Inference only, forever. Gradients here would be a slow memory leak on a
            # path that has no use for them.
            for parameter in model.parameters():
                parameter.requires_grad_(False)
            torch.set_grad_enabled(False)

            self._tokenizer = tokenizer
            self._model = model
            self._n_layers = int(model.config.num_hidden_layers)
            self._hidden = int(model.config.hidden_size)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def n_layers(self) -> int:
        return self._n_layers

    @property
    def hidden_size(self) -> int:
        return self._hidden

    # ------------------------------------------------------------------ #

    def encode(
        self, premises: Sequence[str], hypotheses: Sequence[str], *, batch_size: int = 16
    ) -> EncodedBatch:
        """Pooled hidden states at every layer, for each (premise, hypothesis) pair.

        The pair is fed as a cross-encoder input rather than encoded separately, which
        matters: grounding is a *relation* between the context and the claim, and two
        independent embeddings cannot express one. Separate encoding is what makes a
        similarity-based grounding check fail on a fluent fabrication that reuses the
        passage's vocabulary.
        """
        self.load()
        import torch

        if len(premises) != len(hypotheses):
            raise ValueError(
                f"premises and hypotheses must be the same length, got "
                f"{len(premises)} and {len(hypotheses)}"
            )

        per_layer: list[list[Any]] = [[] for _ in range(self._n_layers + 1)]
        with self._lock, torch.inference_mode():
            for start in range(0, len(premises), batch_size):
                stop = start + batch_size
                encoded = self._tokenizer(
                    list(premises[start:stop]),
                    list(hypotheses[start:stop]),
                    return_tensors="pt",
                    truncation=True,
                    max_length=self.max_tokens,
                    padding=True,
                )
                output = self._model(**encoded, output_hidden_states=True)
                mask = encoded["attention_mask"].unsqueeze(-1).float()
                for index, states in enumerate(output.hidden_states):
                    # Mean over real tokens only. Including padding would make the
                    # representation depend on the longest item in the batch, so the
                    # same input would encode differently depending on what it was
                    # batched with -- which is untraceable when it goes wrong.
                    pooled = (states * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                    per_layer[index].append(pooled.cpu())

        return EncodedBatch(
            layers=[torch.cat(chunks, dim=0).numpy() for chunks in per_layer],
            n_layers=self._n_layers,
            hidden_size=self._hidden,
            model_name=self.model_name,
        )

    def health(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "loaded": self.loaded,
            "layers": self._n_layers,
            "hidden_size": self._hidden,
            "max_tokens": self.max_tokens,
            "generative": False,
        }
