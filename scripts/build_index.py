"""Build the retrieval index over the banking corpus.

Offline, deliberately. The index is opened read-only on the request path, so the only
way a chunk changes is a rebuild -- which makes "what did retrieval see when it answered
that?" a question with an answer.

    uv run python scripts/build_index.py
    uv run python scripts/build_index.py --embedder BAAI/bge-small-en-v1.5

Rebuilding is a full rebuild every time. The corpus is 45 documents; an incremental
path would be more code than the thing it optimises, and a partially-updated index is
the kind of state that produces one wrong answer a week.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.retrieval import (  # noqa: E402
    RetrievalIndex,
    Retriever,
    corpus_chunks,
    load_corpus,
    load_embedder,
    load_manifest,
)

#: Questions the demo actually asks. Printed after every build, because an index that
#: builds without error and retrieves the wrong clause looks identical from the outside.
SMOKE_QUERIES = [
    "Can I prepay my home loan on a floating rate? Is there a charge?",
    "What is the annual fee on the credit card?",
    "My claim was rejected -- how long do I have to appeal?",
    "What are the branch timings in Mumbai?",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "corpus" / "manifest.json")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "corpus.db")
    parser.add_argument(
        "--embedder",
        default="hashing-v1",
        help="'hashing-v1' (default, no torch) or a sentence-transformers model name",
    )
    parser.add_argument("--dim", type=int, default=256, help="hashing embedder width only")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    documents = load_corpus(args.manifest, root=REPO_ROOT)
    chunks = corpus_chunks(documents)
    embedder = load_embedder(args.embedder, dim=args.dim)

    index = RetrievalIndex.build(
        args.out,
        chunks,
        embedder=embedder,
        corpus_version=str(manifest.get("version", "")),
    )
    untrusted = sum(1 for c in chunks if c.provenance.endswith("untrusted"))
    print(
        f"built {args.out.relative_to(REPO_ROOT)}: {len(documents)} documents -> "
        f"{len(chunks)} chunks ({untrusted} untrusted), embedder={embedder.name} "
        f"dim={embedder.dim}"
    )
    if not getattr(embedder, "semantic", False):
        print(
            "  note: the dense arm is a lexical stand-in, not a semantic model. "
            "BM25 is carrying retrieval. Install the 'ml' extra and rebuild with "
            "--embedder BAAI/bge-small-en-v1.5 for real embeddings."
        )

    retriever = Retriever(index=index)
    print()
    for query in SMOKE_QUERIES:
        hits = retriever.search(query, k=3)
        print(f"  {query}")
        for hit in hits:
            flag = " [UNTRUSTED]" if hit.chunk.provenance.endswith("untrusted") else ""
            print(f"    {hit.chunk.chunk_id:9} {hit.found_by:8} {hit.chunk.title[:58]}{flag}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
