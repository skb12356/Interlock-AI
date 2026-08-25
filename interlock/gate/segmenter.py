"""Streaming sentence segmentation.

The gate holds exactly one sentence, so it needs to know about a boundary *the moment
it arrives*, not when the stream ends. That rules out running a batch segmenter over
the finished answer, and it is why this is a stateful accumulator rather than a function.

**The invariant that matters most:** segmentation depends only on the text, never on how
the text happened to arrive over the wire. Providers split tokens arbitrarily — often
mid-word, sometimes mid-number, and in one recorded fixture mid-``<think>`` tag. If
chunking changed the boundaries, the gate would verify different sentences depending on
network timing, which is untestable and unshippable.

Three mechanisms, in order of how often they fire:

1. **Abbreviation-aware boundary detection.** ``pysbd`` when available, with a
   deterministic fallback. A regex on ``[.!?]`` breaks on ``Rs. 40,000``, ``Clause 7.4``
   and ``e.g.`` — and each break is either a sentence the gate waits forever to finish
   or a fragment the verifier flags out of context.
2. **A hard flush** at 240 characters or a blank line. A model that never emits a
   terminator would otherwise hold the gate open until the 8 s watchdog fires, which on
   stage looks exactly like a freeze.
3. **Reasoning-block exclusion** (finding F-004). qwen3 emits ``<think></think>`` even
   when asked not to. That text is not the answer: verifying it against the corpus is
   meaningless, and showing it as the answer's first sentence is worse. It is excluded
   from sentences but *kept*, because a reviewer asking "what was it thinking?" deserves
   an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["HARD_FLUSH_CHARS", "StreamingSegmenter", "split_sentences"]

#: Flush an unterminated sentence at this length. Long enough that real sentences are
#: never cut (the longest in the corpus is ~210 characters), short enough that a runaway
#: generation cannot stall the gate.
HARD_FLUSH_CHARS = 240

#: Reasoning tags emitted by open-weight models. Excluded from answer text.
_REASONING_TAGS = ("think", "thinking", "reasoning")

_OPEN_TAG = re.compile(r"<(" + "|".join(_REASONING_TAGS) + r")\s*>", re.IGNORECASE)
_CLOSE_TAG = re.compile(r"</(" + "|".join(_REASONING_TAGS) + r")\s*>", re.IGNORECASE)

#: The longest possible partial tag we might be holding, e.g. "</reasoning".
_MAX_TAG_LEN = max(len(tag) for tag in _REASONING_TAGS) + 4

try:  # pragma: no cover - depends on which branch is installed
    import pysbd

    _SEGMENTER: object | None = pysbd.Segmenter(language="en", clean=False, char_span=False)
except Exception:  # pragma: no cover
    _SEGMENTER = None


# --------------------------------------------------------------------------- #
# Boundary detection
# --------------------------------------------------------------------------- #

#: Tokens that end in a period without ending a sentence. pysbd knows most of these;
#: the fallback needs them explicitly, and `Rs.` in particular is not in pysbd's
#: English list because it is an Indian convention.
_ABBREVIATIONS = {
    "rs",
    "inr",
    "no",
    "nos",
    "vs",
    "etc",
    "eg",
    "ie",
    "cf",
    "al",
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "sr",
    "jr",
    "st",
    "capt",
    "lt",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
    "a.m",
    "p.m",
    "am",
    "pm",
    "approx",
    "dept",
    "est",
    "fig",
    "min",
    "max",
    "clause",
    "sec",
    "para",
    "art",
    "ch",
    "pg",
    "vol",
    "ltd",
    "pvt",
    "co",
    "corp",
    "inc",
}

_BOUNDARY = re.compile(r"[.!?]+[\"')\]]*(?=\s)|[.!?]+[\"')\]]*$")
_WORD_BEFORE = re.compile(r"([A-Za-z.]+)$")


def _is_real_boundary(text: str, end: int) -> bool:
    """Decide whether the terminator ending at ``end`` closes a sentence."""
    before = text[:end].rstrip("\"')]")
    if not before:
        return False

    # A decimal or a numbered clause: "8.75", "Clause 7.4", "1." at a list start.
    stripped = before.rstrip(".")
    if stripped and stripped[-1].isdigit():
        after = text[end : end + 2]
        # "40,000. That" is a boundary; "7.4 imposes" is not.
        if not after or not after[:1].isspace():
            return False
        following = text[end:].lstrip()
        if following[:1].islower() or following[:1].isdigit():
            return False
        # A bare list marker at the start of a line: "1. Identification"
        line_start = before.rfind("\n") + 1
        if before[line_start:].strip().rstrip(".").isdigit():
            return False

    match = _WORD_BEFORE.search(stripped)
    if match:
        word = match.group(1).lower()
        # Check both forms: "e.g" must normalise to "eg", so internal dots are removed
        # as well as outer ones. Stripping only the outer dots leaves "e.g", which
        # matches nothing and silently splits mid-abbreviation.
        if word.strip(".") in _ABBREVIATIONS or word.replace(".", "") in _ABBREVIATIONS:
            return False

        # A single capital letter is an initial: "J. R. Rao".
        bare = match.group(1).strip(".")
        if len(bare) == 1 and bare.isupper():
            return False

    return True


def split_sentences(text: str) -> tuple[list[str], str]:
    """Split ``text`` into (complete sentences, trailing incomplete remainder).

    Pure, and independent of chunking — which is what makes the streaming wrapper's
    order-independence property hold.
    """
    if not text.strip():
        return [], text

    # Never split inside a fenced code block: it has no sentence structure, and a
    # fragment of one cannot be grounded by the verifier or rewritten by repair.
    if text.count("```") % 2 == 1:
        return [], text

    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        end = match.end()
        if not _is_real_boundary(text, end):
            continue
        if text[start:end].count("```") % 2 == 1:
            continue
        candidate = text[start:end].strip()
        if candidate:
            sentences.append(candidate)
            start = end

    remainder = text[start:]

    # A blank line ends a paragraph even without punctuation -- headings and list
    # blocks routinely have none.
    if "\n\n" in remainder:
        head, _, tail = remainder.rpartition("\n\n")
        head = head.strip()
        if head:
            sentences.append(head)
        remainder = tail

    return sentences, remainder


def _refine_with_pysbd(sentence: str) -> list[str]:
    """Let pysbd split a run our own boundary pass merged, e.g. a numbered list."""
    if _SEGMENTER is None or len(sentence) < 2:
        return [sentence]
    try:  # pragma: no cover - library behaviour
        parts = [str(p).strip() for p in _SEGMENTER.segment(sentence)]  # type: ignore[attr-defined]
    except Exception:
        return [sentence]
    parts = [p for p in parts if p]
    return parts or [sentence]


# --------------------------------------------------------------------------- #
# The streaming wrapper
# --------------------------------------------------------------------------- #


@dataclass
class StreamingSegmenter:
    """Accumulates chunks and emits sentences as soon as they complete."""

    hard_flush_chars: int = HARD_FLUSH_CHARS
    exclude_reasoning: bool = True

    _buffer: str = field(default="", init=False)
    _carry: str = field(default="", init=False)
    _in_reasoning: bool = field(default=False, init=False)
    _reasoning: list[str] = field(default_factory=list, init=False)
    _finished: bool = field(default=False, init=False)

    @property
    def pending(self) -> str:
        """Text held back because its sentence has not finished."""
        return self._buffer

    @property
    def reasoning_text(self) -> str:
        """Everything excluded as reasoning. Kept for the console, never verified."""
        return "".join(self._reasoning)

    def push(self, chunk: str) -> list[str]:
        """Add a chunk; return whatever sentences completed because of it."""
        if not chunk:
            return []
        self._finished = False
        answer_text = self._strip_reasoning(chunk) if self.exclude_reasoning else chunk
        if not answer_text:
            return []

        self._buffer += answer_text
        sentences, self._buffer = split_sentences(self._buffer)

        # A sentence that will not end must not hold the gate open until the watchdog.
        # Loop, because a single push can deliver many times the limit at once -- a
        # non-streaming replay, or a provider that batches its chunks.
        while len(self._buffer) >= self.hard_flush_chars:
            cut = self._hard_cut(self._buffer)
            if not cut:
                break
            sentences.append(cut)
            self._buffer = self._buffer[len(cut) :].lstrip()

        return [part for sentence in sentences for part in _refine_with_pysbd(sentence)]

    def flush(self) -> str | None:
        """Emit whatever is left at the end of the stream, once."""
        if self._finished:
            return None
        self._finished = True
        remaining = (self._carry + self._buffer).strip()
        self._buffer = ""
        self._carry = ""
        return remaining or None

    # -- internals --------------------------------------------------------- #

    def _hard_cut(self, text: str) -> str:
        """Cut at the last word boundary before the limit, so we never split a word."""
        window = text[: self.hard_flush_chars]
        space = window.rfind(" ")
        return (window[:space] if space > self.hard_flush_chars // 2 else window).strip()

    def _strip_reasoning(self, chunk: str) -> str:
        """Remove ``<think>...</think>`` spans, tolerating tags split across chunks.

        The carry buffer is what makes that work: a chunk ending in ``"<th"`` holds
        those characters back rather than emitting them as answer text, because the
        next chunk may complete the tag. Without it, a provider that splits on tokens
        would leak tag fragments into the verified sentence.
        """
        text = self._carry + chunk
        self._carry = ""
        out: list[str] = []

        while text:
            if self._in_reasoning:
                match = _CLOSE_TAG.search(text)
                if match is None:
                    keep, self._carry = self._split_partial_tag(text)
                    self._reasoning.append(keep)
                    return "".join(out)
                self._reasoning.append(text[: match.start()])
                self._in_reasoning = False
                text = text[match.end() :]
                continue

            match = _OPEN_TAG.search(text)
            if match is None:
                keep, self._carry = self._split_partial_tag(text)
                out.append(keep)
                return "".join(out)
            out.append(text[: match.start()])
            self._in_reasoning = True
            text = text[match.end() :]

        return "".join(out)

    @staticmethod
    def _split_partial_tag(text: str) -> tuple[str, str]:
        """Hold back a trailing fragment that might be the start of a tag."""
        index = text.rfind("<")
        if index == -1 or len(text) - index > _MAX_TAG_LEN:
            return text, ""
        if _OPEN_TAG.fullmatch(text[index:]) or _CLOSE_TAG.fullmatch(text[index:]):
            return text, ""
        return text[:index], text[index:]
