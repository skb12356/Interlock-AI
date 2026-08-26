"""The retrieval facade: a question in, labelled ``Fragment`` objects out.

This is deliberately the only surface the rest of the system uses. Two consequences
follow, and both are load-bearing.

**Retrieval never returns bare strings.** Everything comes back as a ``Fragment``
carrying provenance and domain, because a passage without those labels cannot be priced
by the stakes model, cannot be scanned per-chunk by the injection detector, and cannot
be joined by the tool interlock. Strings are how untrusted content gets laundered into
trusted context.

**Untrusted passages are retrieved, not filtered.** It is tempting to drop ``d044``
from the results and call the problem solved. That would demo well and prove nothing:
the point of the poisoned PDF is that it *reaches the context window* and is then
caught by the per-chunk injection scan and stopped at the tool boundary by provenance.
Filtering at retrieval would hide the mechanism the product is actually claiming.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from interlock.core.types import Fragment
from interlock.retrieval.embedder import Embedder, HashingEmbedder
from interlock.retrieval.store import Hit, RetrievalIndex

__all__ = ["RetrievalResult", "Retriever"]

#: Enough evidence for a repair to have something to correct against, few enough that
#: the prompt does not drown the flagged claim in context.
DEFAULT_K = 4


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """What retrieval found, and enough about how to explain it in the console."""

    fragments: list[Fragment]
    hits: list[Hit]
    latency_ms: float = 0.0
    query: str = ""

    @property
    def doc_ids(self) -> list[str]:
        return [hit.chunk.doc_id for hit in self.hits]

    @property
    def untrusted(self) -> list[Fragment]:
        return [f for f in self.fragments if f.provenance.endswith("untrusted")]


@dataclass
class Retriever:
    """Hybrid search over the built index, returning contract types.

    Search is synchronous SQLite work measured in single-digit milliseconds on a
    45-document corpus, so :meth:`retrieve` runs it on a worker thread rather than
    blocking the event loop that is concurrently streaming other requests' tokens.
    """

    index: RetrievalIndex
    k: int = DEFAULT_K

    @classmethod
    def open(
        cls,
        db_path: Path | str,
        *,
        embedder: Embedder | None = None,
        k: int = DEFAULT_K,
    ) -> Retriever:
        return cls(index=RetrievalIndex(db_path, embedder=embedder or HashingEmbedder()), k=k)

    def search(
        self, question: str, *, k: int | None = None, domain: str | None = None
    ) -> list[Hit]:
        return self.index.search(question, k=k or self.k, domain=domain)

    async def retrieve(
        self,
        question: str,
        *,
        k: int | None = None,
        domain: str | None = None,
    ) -> RetrievalResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        hits = await asyncio.to_thread(self.search, question, k=k, domain=domain)
        return RetrievalResult(
            fragments=[hit.chunk.to_fragment(score=hit.score) for hit in hits],
            hits=hits,
            latency_ms=(loop.time() - started) * 1000.0,
            query=question,
        )

    def evidence_for(self, question: str, *, k: int = 3) -> list[str]:
        """Plain passages for the repair prompt, best first.

        Untrusted passages are excluded **here specifically**: this text is fed back to
        a model as ground truth to rewrite against, and a repair that corrects an answer
        into agreement with a poisoned document is worse than no repair at all.
        """
        return [
            hit.chunk.text
            for hit in self.search(question, k=k * 2)
            if not hit.chunk.provenance.endswith("untrusted")
        ][:k]

    def close(self) -> None:
        self.index.close()


@dataclass
class NullRetriever:
    """Stands in when no index has been built, so the gateway still starts.

    Returns nothing rather than raising: a missing index degrades the answer, it does
    not fail the request. The gateway logs it once at startup, because retrieval
    silently returning nothing forever is exactly the kind of thing that gets noticed
    the week after a demo.
    """

    k: int = DEFAULT_K
    reason: str = "no retrieval index"
    fragments: list[Fragment] = field(default_factory=list)

    async def retrieve(self, question: str, **_: object) -> RetrievalResult:
        return RetrievalResult(fragments=[], hits=[], query=question)

    def evidence_for(self, question: str, *, k: int = 3) -> list[str]:
        return []

    def close(self) -> None:
        return None
