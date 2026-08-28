"""The observer probe, as one signal among several.

The probe reaches AUROC 0.945 held-out against 0.833 for the best free lexical check —
a real improvement, and not a reason to throw the lexical checks away. They fail in
different directions, which is the entire argument for keeping both:

* The probe reads *meaning*. It catches a fluent fabrication that reuses the passage's
  vocabulary, which no lexical measure can.
* ``numeric_unsupported`` reads *symbols*. A rupee figure is either in the source or it
  is invented, and the probe — which sees text, not arithmetic — is much weaker on
  exactly that case.

So this ships as an additional feature into the same fusion, not as a replacement. The
calibrator decides how much weight it earns, which is the correct place for that decision
and not this file.

**It is a score, not a probability** (ADR-002). Nothing here may reach the objective
without passing through isotonic calibration first, and the class carries no method that
would tempt a caller into using the raw value.

**Cost.** 123 ms per sentence on CPU. That is real, it is charged to the ``gate_hold``
lane when the commit gate waits on it, and it is why this runs only on buffered traffic:
spending 123 ms checking a branch-hours answer would consume the entire latency budget on
the traffic least likely to need it.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from interlock.core.ids import context_key
from interlock.core.types import Fragment, SignalReading

__all__ = ["PROBE_SIGNAL", "ProbeSignal"]

#: The feature name the calibrator and the fusion see.
PROBE_SIGNAL = "observer.probe"

#: Sentences shorter than this are not worth a 123 ms forward pass. They are almost
#: always acknowledgements, and ``is_claim_bearing`` will have gated them anyway.
MIN_CHARS = 20

# Matches the mock observer and the documented gateway behaviour: repeated sentences
# against the same retrieved context should report a warm prefix rather than a miss.
CONTEXT_CACHE_CAPACITY = 64


@dataclass
class ProbeSignal:
    """Scores one sentence against its retrieved context using the trained probe.

    Both the encoder and the probe bundle are optional. A deployment without them gets
    ``None`` -- explicitly *not* 0.0, because "we did not check" and "we checked and
    found nothing" are different claims and the console has to be able to tell a
    reviewer which one happened.
    """

    encoder: Any = None
    bundle: Any = None
    min_chars: int = MIN_CHARS
    #: Populated on load; stamped onto decisions so a score can be traced to a fit.
    version: str = "none"
    context_cache_capacity: int = CONTEXT_CACHE_CAPACITY
    _failures: int = field(default=0, init=False)
    _context_hits: int = field(default=0, init=False)
    _context_misses: int = field(default=0, init=False)
    _context_cache: OrderedDict[str, str] = field(
        default_factory=OrderedDict, init=False, repr=False
    )

    @classmethod
    def load(cls, probe_path: Path | str, *, model_name: str | None = None) -> ProbeSignal:
        """Load a trained probe, or return an inert signal if there is not one.

        Never raises. A missing probe must degrade the risk engine, not stop the
        gateway -- the deterministic signals still work and are still worth having.
        """
        # Imported here, not at module scope: torch takes ~1.5 s to import and the
        # gateway must start on a machine that does not have it at all.
        from interlock.observer.probes import ProbeBundle

        path = Path(probe_path)
        if not path.exists():
            return cls()
        try:
            bundle = ProbeBundle.load(path)
        except Exception:
            return cls()

        import hashlib

        from interlock.observer.encoder import ObserverEncoder

        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return cls(
            encoder=ObserverEncoder(model_name=model_name or bundle.model_name),
            bundle=bundle,
            version=f"probe@sha256:{digest}",
        )

    @property
    def available(self) -> bool:
        return self.encoder is not None and self.bundle is not None

    # ------------------------------------------------------------------ #

    def score(self, sentence: str, context: Sequence[Fragment]) -> float | None:
        """Raw probe score in [0, 1], or None when it did not run.

        Untrusted passages are excluded from the premise. A poisoned document in the
        premise would let an attacker make their own claim look entailed -- the probe
        would faithfully report that the answer follows from the context, which is true
        and useless when the context is the attack.
        """
        if not self.available or len(sentence.strip()) < self.min_chars:
            return None

        premise = self._premise(context)
        if not premise.strip():
            # No trusted context. The lexical signals already treat this as maximally
            # unsupported; the probe has nothing to compare against and says so.
            return None

        try:
            batch = self.encoder.encode([premise], [sentence], batch_size=1)
            value = float(self.bundle.score(batch.layers[self.bundle.best_layer])[0])
        except Exception:
            # A model that failed to run is a missing signal, never a clean one.
            self._failures += 1
            return None
        return max(0.0, min(1.0, value))

    def _premise(self, context: Sequence[Fragment]) -> str:
        """Trusted context text, cached under the same key the observer API uses."""
        key = context_key(context)
        cached = self._context_cache.get(key)
        if cached is not None:
            self._context_hits += 1
            self._context_cache.move_to_end(key)
            return cached

        self._context_misses += 1
        premise = "\n".join(
            fragment.text
            for fragment in context
            if not str(fragment.provenance).endswith("untrusted")
        )
        if self.context_cache_capacity > 0:
            self._context_cache[key] = premise
            if len(self._context_cache) > self.context_cache_capacity:
                self._context_cache.popitem(last=False)
        return premise

    def reading(
        self, sentence: str, context: Sequence[Fragment], *, latency_ms: float = 0.0
    ) -> SignalReading | None:
        value = self.score(sentence, context)
        if value is None:
            return None
        # prob stays None: this is raw, and only the calibrator may set prob (ADR-002).
        return SignalReading(name=PROBE_SIGNAL, raw=value, latency_ms=latency_ms)

    def health(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "version": self.version,
            "layer": getattr(self.bundle, "best_layer", None),
            "held_out_auroc": getattr(self.bundle, "best_auroc", None),
            "failures": self._failures,
            "context_cache": {
                "capacity": self.context_cache_capacity,
                "size": len(self._context_cache),
                "hits": self._context_hits,
                "misses": self._context_misses,
            },
        }
