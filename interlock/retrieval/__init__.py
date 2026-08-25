"""Retrieval over the banking corpus.

Exists so that L2 repair has something real to correct against (finding F-010: a repair
with no retrieved evidence restates the original -- correct, and useless).
"""

from interlock.retrieval.chunker import Chunk, chunk_markdown
from interlock.retrieval.corpus import CorpusDocument, corpus_chunks, load_corpus, load_manifest
from interlock.retrieval.embedder import (
    Embedder,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    load_embedder,
)
from interlock.retrieval.retriever import NullRetriever, RetrievalResult, Retriever
from interlock.retrieval.store import Hit, RetrievalIndex

__all__ = [
    "Chunk",
    "CorpusDocument",
    "Embedder",
    "HashingEmbedder",
    "Hit",
    "NullRetriever",
    "RetrievalIndex",
    "RetrievalResult",
    "Retriever",
    "SentenceTransformerEmbedder",
    "chunk_markdown",
    "corpus_chunks",
    "load_corpus",
    "load_embedder",
    "load_manifest",
]
