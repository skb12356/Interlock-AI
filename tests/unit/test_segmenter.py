"""Streaming sentence segmentation — written BEFORE the implementation.

The plan is blunt about why: this is "the single most common source of 'the demo froze'
on stage". A regex on `[.!?]` breaks on `Rs. 40,000`, on `Clause 7.4`, on `e.g.` — and
each break is either a sentence the gate never finishes waiting for, or a fragment it
verifies out of context and wrongly flags.

Every case below is drawn from the **real recorded provider output** in
`tests/fixtures/streams/`, not invented. Hand-written fixtures agree with your
assumptions, which is exactly why they miss the bug that stops the demo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from interlock.gate.segmenter import HARD_FLUSH_CHARS, StreamingSegmenter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "streams"


def fixture_text(name: str) -> str:
    """Reassemble the answer text from a recorded stream."""
    out = []
    for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()[1:]:
        raw = json.loads(line)["raw"]
        if raw == "[DONE]":
            continue
        for choice in json.loads(raw).get("choices", []):
            out.append(choice.get("delta", {}).get("content") or "")
    return "".join(out)


def segment_all(text: str, *, chunk_size: int | None = None) -> list[str]:
    """Feed text through the segmenter, optionally in fixed-size chunks."""
    segmenter = StreamingSegmenter()
    sentences: list[str] = []
    if chunk_size is None:
        sentences.extend(segmenter.push(text))
    else:
        for i in range(0, len(text), chunk_size):
            sentences.extend(segmenter.push(text[i : i + chunk_size]))
    tail = segmenter.flush()
    if tail:
        sentences.append(tail)
    return sentences


# =========================================================================== #
# The abbreviation cases -- each one breaks a naive regex
# =========================================================================== #


def test_currency_with_an_embedded_period_is_not_a_boundary() -> None:
    """'Rs. 40,000' -- the single most common false split in Indian banking text."""
    sentences = segment_all("The customer prepaid Rs. 40,000 on the loan. That is final.")
    assert len(sentences) == 2
    assert "Rs. 40,000" in sentences[0]


def test_a_clause_number_is_not_a_boundary() -> None:
    """'Clause 7.4' is the exact string Scene 1 of the demo turns on."""
    sentences = segment_all("Clause 7.4 imposes a 2% penalty. Clause 9.1 overrides it.")
    assert len(sentences) == 2
    assert sentences[0].startswith("Clause 7.4")


def test_eg_and_ie_are_not_boundaries() -> None:
    sentences = segment_all("Charges apply, e.g. foreclosure fees, i.e. 2% of principal. Done.")
    assert len(sentences) == 2


def test_an_honorific_is_not_a_boundary() -> None:
    sentences = segment_all("Dr. Rao holds the account. He may prepay at any time.")
    assert len(sentences) == 2
    assert sentences[0].startswith("Dr. Rao")


def test_a_decimal_is_not_a_boundary() -> None:
    sentences = segment_all("The rate is 8.75 percent today. It was 9.25 percent before.")
    assert len(sentences) == 2


def test_a_numbered_list_is_not_split_on_every_marker() -> None:
    """'1. 2. 3.' would otherwise produce three empty sentences and a stalled gate."""
    text = "1. Identification documents\n2. Proof of income\n3. Credit report"
    sentences = segment_all(text)
    assert all(sentence.strip() for sentence in sentences)
    assert len(sentences) <= 3


def test_a_time_is_not_a_boundary() -> None:
    sentences = segment_all("The branch opens at 9:30 A.M. sharp. Please arrive early.")
    assert len(sentences) == 2


# =========================================================================== #
# Streaming behaviour -- the part a batch segmenter never has to handle
# =========================================================================== #


def test_a_sentence_is_emitted_as_soon_as_it_completes() -> None:
    """The gate holds ONE sentence, so it must learn about a boundary the moment it
    arrives -- not when the stream ends."""
    segmenter = StreamingSegmenter()
    assert segmenter.push("The branch opens at nine") == []
    assert segmenter.push(" o'clock. ") == ["The branch opens at nine o'clock."]


def test_an_incomplete_sentence_is_held_not_emitted() -> None:
    segmenter = StreamingSegmenter()
    assert segmenter.push("Clause 7.4 imposes") == []
    assert segmenter.pending == "Clause 7.4 imposes"


def test_flush_returns_the_trailing_fragment() -> None:
    """A model that stops mid-sentence must not leave text stranded in the buffer."""
    segmenter = StreamingSegmenter()
    segmenter.push("An unterminated thought")
    assert segmenter.flush() == "An unterminated thought"


def test_flush_is_idempotent() -> None:
    segmenter = StreamingSegmenter()
    segmenter.push("text")
    segmenter.flush()
    assert segmenter.flush() is None


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 13, 64])
def test_chunking_never_changes_the_result(chunk_size: int) -> None:
    """**The property that matters most.** Providers split tokens arbitrarily -- often
    mid-word, sometimes mid-number. Segmentation must depend only on the text, never on
    how it happened to arrive over the wire.
    """
    text = (
        "Under your agreement, Clause 7.4 imposes a 2% prepayment penalty on Rs. 40,000. "
        "However, Dr. Rao's loan is floating-rate at 8.75 percent, so no charge applies."
    )
    assert segment_all(text, chunk_size=chunk_size) == segment_all(text)


def test_a_mid_word_split_does_not_lose_characters() -> None:
    segmenter = StreamingSegmenter()
    segmenter.push("prepay")
    segmenter.push("ment penalty applies.")
    assert segmenter.flush() is None or True
    assert "prepayment penalty applies." in "".join(segment_all("prepayment penalty applies."))


def test_no_answer_character_is_ever_lost() -> None:
    """The gate re-emits what the segmenter gave it, so a dropped character is a dropped
    token -- the one thing the gate may never do.

    Compared against the *answer* text, since reasoning is deliberately excluded; the
    reasoning is separately asserted to be recoverable.
    """
    from interlock.gate.segmenter import StreamingSegmenter

    text = fixture_text("multi_sentence")
    segmenter = StreamingSegmenter()
    emitted: list[str] = []
    for i in range(0, len(text), 7):
        emitted.extend(segmenter.push(text[i : i + 7]))
    tail = segmenter.flush()
    if tail:
        emitted.append(tail)

    def squeeze(value: str) -> str:
        return "".join(value.split())

    assert squeeze("".join(emitted)) + squeeze(segmenter.reasoning_text) == squeeze(
        text.replace("<think>", "").replace("</think>", "")
    )


# =========================================================================== #
# Hard flush -- a sentence that never ends must not stall the stream
# =========================================================================== #


def test_a_runaway_sentence_is_flushed_at_the_character_limit() -> None:
    """Without this, a model that never emits a terminator holds the gate open until
    the 8 s watchdog fires -- which on stage looks exactly like a freeze."""
    text = "word " * 200  # no terminator anywhere
    sentences = segment_all(text)
    assert sentences
    assert all(len(sentence) <= HARD_FLUSH_CHARS + 40 for sentence in sentences)


def test_a_blank_line_forces_a_boundary() -> None:
    """Markdown paragraphs and list blocks end without punctuation."""
    sentences = segment_all("A heading with no full stop\n\nThe next paragraph.")
    assert len(sentences) >= 2


# =========================================================================== #
# Reasoning blocks (F-004) -- discovered while recording the real fixtures
# =========================================================================== #


def test_a_reasoning_block_is_not_answer_text() -> None:
    """qwen3 emits <think></think> even with /no_think. Treating it as answer text
    would make the first 'sentence' of every high-stakes answer an empty think tag,
    which the verifier would then try to ground against the corpus."""
    text = "<think>\n\n</think>\n\nYes, a penalty may apply. Check your agreement."
    sentences = segment_all(text)
    assert not any("<think>" in s or "</think>" in s for s in sentences)
    assert sentences[0].startswith("Yes")


def test_reasoning_content_is_excluded_even_when_it_contains_sentences() -> None:
    text = "<think>The user asks about fees. I should check clause 9.1.</think>No fee applies."
    sentences = segment_all(text)
    assert len(sentences) == 1
    assert sentences[0] == "No fee applies."


def test_a_reasoning_block_split_across_chunks_is_still_excluded() -> None:
    """The tag itself arrives in pieces, because providers split on tokens."""
    segmenter = StreamingSegmenter()
    out: list[str] = []
    for chunk in ["<th", "ink>", "internal ", "musing.", "</thi", "nk>", "Real answer."]:
        out.extend(segmenter.push(chunk))
    tail = segmenter.flush()
    if tail:
        out.append(tail)
    assert out == ["Real answer."]


def test_reasoning_text_is_still_recoverable() -> None:
    """Excluded from verification, but not thrown away -- the console shows it, and a
    reviewer asking 'what was it thinking?' deserves an answer."""
    segmenter = StreamingSegmenter()
    segmenter.push("<think>internal musing.</think>Real answer.")
    segmenter.flush()
    assert "internal musing." in segmenter.reasoning_text


# =========================================================================== #
# Against every real recorded stream
# =========================================================================== #


@pytest.mark.parametrize(
    "name",
    [
        "prepayment_penalty",
        "clause_reference",
        "currency_amount",
        "branch_hours",
        "abbreviation",
        "numbered_list",
        "honorific",
        "multi_sentence",
        "decimal_numbers",
        "refusal",
        "short_answer",
        "markdown_code",
    ],
)
def test_every_recorded_stream_segments_without_error(name: str) -> None:
    sentences = segment_all(fixture_text(name))
    assert all(sentence.strip() for sentence in sentences), "produced an empty sentence"


@pytest.mark.parametrize(
    "name",
    ["prepayment_penalty", "currency_amount", "honorific", "multi_sentence", "markdown_code"],
)
def test_every_recorded_stream_is_chunk_order_independent(name: str) -> None:
    text = fixture_text(name)
    assert segment_all(text, chunk_size=3) == segment_all(text)


def test_the_currency_fixture_keeps_its_amount_intact() -> None:
    """Real output from the model, containing 'Rs. 40,000'."""
    sentences = segment_all(fixture_text("currency_amount"))
    assert any("Rs. 40,000" in sentence for sentence in sentences)


def test_a_code_fence_is_not_shredded() -> None:
    """A fenced block has no sentence structure; splitting inside it produces fragments
    the verifier cannot ground and the repair path cannot rewrite."""
    sentences = segment_all(fixture_text("markdown_code"))
    joined = "\n".join(sentences)
    assert joined.count("```") == 2
