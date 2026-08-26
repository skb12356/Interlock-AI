"""Dense embeddings, behind one interface, with a deterministic default.

The plan specifies ``bge-small-en-v1.5``. That is a 130 MB model on top of a ~2.5 GB
torch install, and this build deliberately does not pay that cost before it is needed
(the same pattern already used for the injection detector: deterministic backend by
default, ML backend imported lazily when it is present).

So the default is a **hashed lexical embedding** -- honest about what it is. It is not
a semantic model: it will not connect "foreclosure" to "prepayment" on its own. What it
does provide is a real dense vector with stable dimension, so the vector store, the
fusion, the index format and every caller are exercised exactly as they will be with
the real model. Swapping in ``bge-small-en-v1.5`` changes one config string.

The semantic gap this leaves is covered on the retrieval side rather than pretended
away: lexical FTS5/BM25 runs alongside the dense arm and the two are fused, and BM25
over 45 clause-formatted banking documents is genuinely strong. See
``retrieval/store.py``.

**Hashing is done with blake2b, not ``hash()``.** Python salts ``hash()`` per process,
so an index built in one process would silently fail to match queries embedded in the
next. That is the kind of bug that shows up as "retrieval quality regressed" weeks later.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Protocol, runtime_checkable

__all__ = ["Embedder", "HashingEmbedder", "SentenceTransformerEmbedder", "load_embedder"]

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")

#: Dropped before hashing, not before BM25. BM25 has IDF and down-weights these
#: itself; a hashed bag of words has no corpus statistics at all, so without this
#: the vector is dominated by "the customer may on a" and every banking document
#: looks alike. Observed directly: the untrimmed vector ranked a home-loan rate
#: card above the prepayment clause for the question "can I prepay my home loan".
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "shall",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into a fixed-width unit vector."""

    #: Vector width. Stamped into the index; a mismatch is refused rather than coerced.
    dim: int
    #: Recorded in the index so a rebuild is detectable, and reportable in the console.
    name: str
    #: Whether this arm actually models meaning. The fusion weights the dense arm
    #: by this: a lexical stand-in should break ties, not outvote BM25.
    semantic: bool

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping clause numbers (``9.1``) intact as one token."""
    return _TOKEN.findall(text.lower())


@dataclass
class HashingEmbedder:
    """Sublinear-tf hashed bag of unigrams and bigrams, L2-normalised.

    Deterministic across processes and machines, needs no model download, and produces
    a genuine cosine space -- just a lexical one.
    """

    dim: int = 256
    name: str = "hashing-v1"
    #: False, and said so out loud. This is a lexical vector wearing a dense
    #: interface; claiming otherwise would quietly overstate retrieval quality in
    #: every number measured downstream.
    semantic: bool = False
    #: Bigrams let "prepayment charge" score above two independent word matches.
    use_bigrams: bool = True

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        tokens = [t for t in tokenize(text) if t not in _STOPWORDS]
        if self.use_bigrams:
            tokens = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
        counts = Counter(tokens)
        vector = [0.0] * self.dim
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            # The low bit of the second half decides the sign, so unrelated tokens
            # colliding in the same bucket cancel on average instead of compounding.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


@dataclass
class SentenceTransformerEmbedder:
    """``bge-small-en-v1.5`` (or any sentence-transformers model), imported lazily.

    Only constructed when the config asks for it, so a machine without torch still
    starts the gateway. BGE wants a query instruction prefix and symmetric passage
    text; getting that backwards costs several points of recall.
    """

    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    semantic: bool = True
    name: str = field(init=False, default="")
    query_prefix: str = "Represent this sentence for searching relevant passages: "
    _model: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.name = self.model_name

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self.dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(list(texts), normalize_embeddings=True)
        return [[float(value) for value in row] for row in vectors]

    def encode_query(self, text: str) -> list[float]:
        return self.encode([f"{self.query_prefix}{text}"])[0]


def load_embedder(name: str = "hashing-v1", *, dim: int = 256) -> Embedder:
    """Resolve a config string to an embedder, falling back loudly, never silently."""
    if name in {"hashing-v1", "hash", ""}:
        return HashingEmbedder(dim=dim)
    return SentenceTransformerEmbedder(model_name=name)


def embed_query(embedder: Embedder, text: str) -> list[float]:
    """Use the model's query-side encoding when it has one (BGE does; hashing does not)."""
    encode_query = getattr(embedder, "encode_query", None)
    if callable(encode_query):
        return list(encode_query(text))
    return embedder.encode([text])[0]
