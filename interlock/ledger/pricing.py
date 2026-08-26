"""What a token actually costs, per model, in rupees.

Until now the whole system priced compute with one blended
``price_inr_per_1k_tokens``. That was fine while every request went to the same local
model, and it becomes wrong the moment routing works: the entire spend argument is
*"cheap traffic goes to a cheap model"*, and a blended price makes that saving
arithmetically invisible. A router that moves 80% of traffic to a model costing a fifth
as much shows a 0% saving if both are billed at the same rate.

Three things this file is careful about, because each one is a way to report a number
that is precise and false:

**Prompt and completion tokens are priced separately.** Every hosted provider charges
3–5× more for completion, and RAG requests are prompt-heavy — often 800 prompt tokens
against 100 completion. Averaging the two rates over-states the cost of exactly the
traffic Interlock is trying to make cheap.

**Local models are not free.** They are *unmetered*, which is a different thing. A
qwen3:8b generation occupies a GPU for 30 seconds and that has a cost, even though no
invoice arrives. Pricing local models at zero would make every efficiency claim
trivially true and completely meaningless, so they carry an imputed amortised rate with
its basis written down.

**A model nobody priced is reported, not guessed at.** ``unknown_models`` accumulates
names that fell through to the default, and the ledger surfaces it. Silently applying a
default rate to a model somebody added last week is how a spend report drifts away from
the invoice without anyone noticing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["ModelPrice", "PriceBook", "load_price_book"]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Rupees per 1,000 tokens, split by direction."""

    model: str
    prompt_inr_per_1k: float
    completion_inr_per_1k: float
    #: Free text: where this number came from. A price with no provenance is a guess
    #: wearing a decimal point, and these end up in a spend report an auditor reads.
    basis: str = ""
    #: True for locally-hosted models, whose cost is imputed rather than invoiced.
    imputed: bool = False

    def cost_inr(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> float:
        return (
            prompt_tokens * self.prompt_inr_per_1k / 1000.0
            + completion_tokens * self.completion_inr_per_1k / 1000.0
        )


#: Defaults for this deployment. USD figures converted at Rs.84/USD, stated so the
#: conversion can be redone rather than reverse-engineered.
#:
#: The local rates are the interesting ones. qwen3:8b occupies this laptop's GPU for
#: ~30 s to produce ~100 completion tokens. Imputing even a modest Rs.40/hour of
#: amortised hardware makes that Rs.0.33 per generation, or ~Rs.3.30 per 1k completion
#: tokens. Rounded to Rs.3.00, and 1/4 of that on the prompt side since prefill is
#: dramatically cheaper per token than decode. qwen3:4b is roughly half the model, so
#: roughly half the rate.
DEFAULT_PRICES: tuple[ModelPrice, ...] = (
    ModelPrice(
        model="qwen3:4b",
        prompt_inr_per_1k=0.35,
        completion_inr_per_1k=1.40,
        basis="imputed: ~Rs.40/hr amortised local GPU, ~2x faster than the 8b",
        imputed=True,
    ),
    ModelPrice(
        model="qwen3:8b",
        prompt_inr_per_1k=0.75,
        completion_inr_per_1k=3.00,
        basis="imputed: ~Rs.40/hr amortised local GPU, measured ~30s per 100 tokens",
        imputed=True,
    ),
    ModelPrice(
        model="gpt-4o",
        prompt_inr_per_1k=0.21,
        completion_inr_per_1k=0.84,
        basis="USD 2.50/1M prompt, 10.00/1M completion at Rs.84/USD",
    ),
    ModelPrice(
        model="gpt-4o-mini",
        prompt_inr_per_1k=0.0126,
        completion_inr_per_1k=0.0504,
        basis="USD 0.15/1M prompt, 0.60/1M completion at Rs.84/USD",
    ),
    ModelPrice(
        model="claude-sonnet-4",
        prompt_inr_per_1k=0.252,
        completion_inr_per_1k=1.26,
        basis="USD 3.00/1M prompt, 15.00/1M completion at Rs.84/USD",
    ),
    ModelPrice(
        model="claude-haiku-4-5",
        prompt_inr_per_1k=0.084,
        completion_inr_per_1k=0.42,
        basis="USD 1.00/1M prompt, 5.00/1M completion at Rs.84/USD",
    ),
)

#: Applied to a model nobody has priced. Deliberately **not** zero and not cheap: an
#: unpriced model should make the bill look worse than it is, so somebody goes and
#: prices it. A conservative default that under-reports would never get noticed.
FALLBACK = ModelPrice(
    model="__default__",
    prompt_inr_per_1k=0.75,
    completion_inr_per_1k=3.00,
    basis="UNPRICED MODEL -- billed at the local strong tier's rate as a placeholder",
)


@dataclass
class PriceBook:
    """Model name → price, with the misses recorded."""

    prices: dict[str, ModelPrice] = field(default_factory=dict)
    fallback: ModelPrice = FALLBACK
    #: Models that fell through to the fallback, and how often. Surfaced, never silent.
    unknown_models: dict[str, int] = field(default_factory=dict)

    @classmethod
    def default(cls) -> PriceBook:
        return cls(prices={price.model: price for price in DEFAULT_PRICES})

    def price_for(self, model: str | None) -> ModelPrice:
        name = (model or "").strip()
        found = self.prices.get(name)
        if found is not None:
            return found
        # Tolerate a version suffix: 'gpt-4o-2024-11-20' should price as 'gpt-4o'
        # rather than as an unknown model, because a provider renaming a snapshot is
        # not the same event as somebody adding an unpriced model.
        for known, price in self.prices.items():
            if name.startswith(known):
                return price
        if name:
            self.unknown_models[name] = self.unknown_models.get(name, 0) + 1
        return self.fallback

    def cost_inr(
        self, model: str | None, *, prompt_tokens: int = 0, completion_tokens: int = 0
    ) -> float:
        return self.price_for(model).cost_inr(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )

    def cheaper_of(self, left: str, right: str) -> str:
        """Which of two models costs less on a typical RAG-shaped request?

        Weighted 800 prompt / 100 completion rather than 1:1, because that is what this
        workload actually looks like and a 1:1 comparison would rank models by their
        completion rate alone.
        """
        cost_left = self.cost_inr(left, prompt_tokens=800, completion_tokens=100)
        cost_right = self.cost_inr(right, prompt_tokens=800, completion_tokens=100)
        return left if cost_left <= cost_right else right

    def report(self) -> dict[str, Any]:
        return {
            "priced_models": sorted(self.prices),
            "imputed_models": sorted(m for m, p in self.prices.items() if p.imputed),
            "unknown_models": dict(self.unknown_models),
            "fallback_basis": self.fallback.basis,
        }


def load_price_book(path: Path | str | None = None) -> PriceBook:
    """Load prices from JSON, or fall back to the built-in defaults.

    Config-driven per D4-A1, but with working defaults so a clean checkout still
    produces a spend report. A file that exists and is malformed raises rather than
    silently reverting -- quietly ignoring a price file somebody wrote is how a
    deployment ends up reporting on rates nobody intended.
    """
    book = PriceBook.default()
    if path is None:
        return book
    path = Path(path)
    if not path.exists():
        return book

    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload.get("models", []):
        price = ModelPrice(
            model=str(entry["model"]),
            prompt_inr_per_1k=float(entry["prompt_inr_per_1k"]),
            completion_inr_per_1k=float(entry["completion_inr_per_1k"]),
            basis=str(entry.get("basis", "")),
            imputed=bool(entry.get("imputed", False)),
        )
        book.prices[price.model] = price
    return book
