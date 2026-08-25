"""Read the corpus manifest and turn it into chunks.

The manifest is the authority for the two labels that cannot be recovered later:

* ``provenance`` -- ``d044`` is a poisoned PDF and ``d045`` is a benign untrusted
  control. Both are ``retrieved_untrusted``. Nothing downstream re-derives this; the
  tool interlock joins on it, and a guess would make the join a guess.
* ``domain`` -- the stakes model prices from what was actually *retrieved* rather than
  from what the question claimed to be about, which is the part an attacker controls.

Loading is strict on purpose. A document listed in the manifest whose file is missing
raises; a file on disk that is missing from the manifest raises too. A corpus that is
quietly one document short changes every number measured against it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from interlock.core.types import PROVENANCE_ORDER, Provenance
from interlock.retrieval.chunker import Chunk, chunk_markdown

__all__ = ["CorpusDocument", "load_corpus", "load_manifest"]


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    doc_id: str
    title: str
    domain: str
    provenance: Provenance
    path: Path
    text: str
    contradicts: str | None = None
    tags: tuple[str, ...] = ()


def load_manifest(manifest_path: Path | str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(Path(manifest_path).read_text(encoding="utf-8")))


def load_corpus(
    manifest_path: Path | str,
    *,
    root: Path | None = None,
    strict: bool = True,
) -> list[CorpusDocument]:
    """Every document the manifest declares, with its labels attached."""
    manifest_path = Path(manifest_path)
    root = root or manifest_path.parent.parent
    manifest = load_manifest(manifest_path)

    documents: list[CorpusDocument] = []
    seen: set[Path] = set()
    for entry in manifest.get("documents", []):
        path = (root / str(entry["path"])).resolve()
        if not path.exists():
            raise FileNotFoundError(f"manifest lists {entry['doc_id']} at {path}, which is missing")
        provenance = str(entry.get("provenance", "retrieved_verified"))
        if provenance not in PROVENANCE_ORDER:
            raise ValueError(f"{entry['doc_id']}: unknown provenance {provenance!r}")
        # mypy narrows to Provenance from the membership check above -- the validation
        # and the type narrowing are the same line, which is the point.
        seen.add(path)
        documents.append(
            CorpusDocument(
                doc_id=str(entry["doc_id"]),
                title=str(entry.get("title", "")),
                domain=str(entry.get("domain", "general")),
                provenance=provenance,
                path=path,
                text=path.read_text(encoding="utf-8"),
                contradicts=entry.get("contradicts"),
                tags=tuple(entry.get("tags", ())),
            )
        )

    if strict:
        on_disk = {p.resolve() for p in manifest_path.parent.glob("*.md")}
        orphans = sorted(p.name for p in on_disk - seen)
        if orphans:
            raise ValueError(
                f"{len(orphans)} document(s) on disk are absent from the manifest "
                f"and would never be retrieved: {', '.join(orphans)}"
            )
    return documents


def corpus_chunks(documents: list[CorpusDocument], **chunk_kwargs: Any) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(
            chunk_markdown(
                document.text,
                doc_id=document.doc_id,
                domain=document.domain,
                provenance=document.provenance,
                title=document.title,
                **chunk_kwargs,
            )
        )
    return chunks
