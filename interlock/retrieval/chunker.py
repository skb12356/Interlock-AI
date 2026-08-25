"""Split corpus documents into citable passages.

Two rules do all the work here, and both exist because of what happens downstream.

**Every chunk carries its heading.** The clause identifier lives in the title line
(``Home Loan Agreement - Clause 9.1``), not in the prose. A chunk split away from its
heading is un-citable: L1 annotate has nothing to put in brackets and L2 repair cannot
tell the model *which* clause it is correcting against. So the heading is prefixed to
the embedded text rather than stored beside it.

**Provenance and domain are attached at ingestion, never inferred later.** This is
invariant 5's precondition: the tool interlock joins over the provenance of the
fragment that triggered an action, so if the label is guessed at read time the join is
guessing too. The manifest is the authority, and ``d044`` -- the poisoned PDF -- comes
back labelled ``retrieved_untrusted`` from the moment it enters the index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from interlock.core.types import Fragment, Provenance

__all__ = ["Chunk", "chunk_markdown"]

#: Long enough to hold a whole clause, short enough that a citation points somewhere
#: specific. The corpus documents are clause-sized already, so most produce one chunk.
DEFAULT_MAX_CHARS = 700
#: Below this a paragraph is merged forward rather than standing alone: a two-line
#: fragment retrieves badly and cites worse.
DEFAULT_MIN_CHARS = 120

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)
_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One indexed passage, and everything needed to cite and price it."""

    doc_id: str
    chunk_id: str
    title: str
    text: str  # heading + body: what is embedded, searched and shown
    body: str  # the passage alone, for display where the heading is already on screen
    domain: str
    provenance: Provenance
    ordinal: int

    def to_fragment(self, score: float | None = None) -> Fragment:
        """The contract type. Retrieval's whole output surface is ``Fragment``."""
        return Fragment(
            text=self.text,
            provenance=self.provenance,
            role="retrieved",
            doc_id=self.chunk_id,
            score=score,
            domain=self.domain,
        )


def extract_title(markdown: str, fallback: str = "") -> str:
    """First ATX heading, or the fallback the manifest supplied."""
    match = _HEADING.search(markdown)
    return match.group("title").strip() if match else fallback


def chunk_markdown(
    markdown: str,
    *,
    doc_id: str,
    domain: str,
    provenance: Provenance,
    title: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[Chunk]:
    """Paragraph-aligned chunks, each prefixed with the document heading."""
    heading = extract_title(markdown, fallback=title)
    body = _HEADING.sub("", markdown).strip()

    paragraphs = [p.strip() for p in _PARA_SPLIT.split(body) if p.strip()]
    merged = _merge_short(paragraphs, min_chars=min_chars, max_chars=max_chars)

    passages: list[str] = []
    for paragraph in merged:
        passages.extend(_split_long(paragraph, max_chars=max_chars))

    if not passages:
        # A document that is nothing but a heading is still a document; indexing it
        # empty would silently drop it from every search.
        passages = [heading] if heading else []

    return [
        Chunk(
            doc_id=doc_id,
            chunk_id=f"{doc_id}#{ordinal}",
            title=heading,
            text=f"{heading}\n\n{passage}" if heading else passage,
            body=passage,
            domain=domain,
            provenance=provenance,
            ordinal=ordinal,
        )
        for ordinal, passage in enumerate(passages)
    ]


def _merge_short(paragraphs: list[str], *, min_chars: int, max_chars: int) -> list[str]:
    out: list[str] = []
    for paragraph in paragraphs:
        if out and len(out[-1]) < min_chars and len(out[-1]) + len(paragraph) + 2 <= max_chars:
            out[-1] = f"{out[-1]}\n\n{paragraph}"
        else:
            out.append(paragraph)
    return out


def _split_long(paragraph: str, *, max_chars: int) -> list[str]:
    """Oversized paragraphs break on sentence boundaries, never mid-sentence.

    A chunk cut mid-sentence retrieves on half a claim and cites the half that was
    kept, which is a worse failure than a chunk that runs slightly long.
    """
    if len(paragraph) <= max_chars:
        return [paragraph]

    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > max_chars:
            out.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        out.append(current)
    return out
