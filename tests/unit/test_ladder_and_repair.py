"""L1 annotate and L2 repair.

The ladder exists so the optimiser has cheap moves available. L1 in particular must stay
free — the moment annotation needs a generation, term (3) of the objective swallows the
rung and the ladder collapses back towards block-or-allow, which is the failure that
gets guardrails switched off in week two.
"""

from __future__ import annotations

from typing import Any

import pytest

from interlock.core.types import Decision, Defect, Fragment, RepairHint, Stakes
from interlock.gate.ladder import Annotator, build_citation
from interlock.gate.repair import SentenceRepairer, build_repair_messages


def _decision(
    action: str = "L1_annotate",
    probs: dict[Defect, float] | None = None,
    hint: RepairHint | None = None,
) -> Decision:
    return Decision(
        decision_id="dec_1",
        action=action,  # type: ignore[arg-type]
        loss_table=[],
        chosen_loss=0.0,
        probs=probs or {},
        repair_hint=hint,
    )


def _stakes() -> Stakes:
    return Stakes(impact_inr=40_000, reversibility="costly", domain="prepayment", confidence=0.9)


# =========================================================================== #
# L1 -- deterministic annotation
# =========================================================================== #


def test_a_clean_sentence_is_left_alone() -> None:
    """No signal, no change. L1 must not editorialise for its own sake."""
    sentence = "Your loan is on a floating rate."
    assert Annotator().annotate(sentence, _decision()) == sentence


def test_an_ungrounded_sentence_is_marked_unverified() -> None:
    result = Annotator().annotate(
        "Clause 7.4 imposes a 2% prepayment penalty.",
        _decision(probs={"ungrounded": 0.62}),
    )
    assert "not verified against the retrieved documents" in result


def test_the_marker_is_about_our_checking_not_about_the_claim() -> None:
    """We know the claim is unverified. We do not know that it is false, and saying so
    would be a stronger statement than the evidence supports."""
    result = Annotator().annotate("A claim.", _decision(probs={"ungrounded": 0.9}))
    assert "false" not in result.lower()
    assert "wrong" not in result.lower()
    assert "not verified" in result.lower()


def test_a_low_probability_does_not_trigger_the_marker() -> None:
    result = Annotator().annotate("A claim.", _decision(probs={"ungrounded": 0.05}))
    assert "not verified" not in result


def test_a_citation_is_appended_from_the_evidence() -> None:
    hint = RepairHint(
        span=(0, 10),
        unsupported_claim="Clause 7.4 imposes a 2% penalty",
        evidence=["Clause 9.1 states no prepayment charge applies to floating-rate loans."],
    )
    result = Annotator().annotate("No charge applies.", _decision(hint=hint))
    assert "(Clause 9.1)" in result


def test_the_citation_goes_before_the_full_stop() -> None:
    """'No charge applies (Clause 9.1).' reads as a sentence.
    'No charge applies. (Clause 9.1)' reads as a footnote someone forgot to place."""
    hint = RepairHint(span=(0, 5), unsupported_claim="x", evidence=["Clause 9.1 states ..."])
    result = Annotator().annotate("No charge applies.", _decision(hint=hint))
    assert result.endswith("(Clause 9.1).")


def test_a_citation_falls_back_to_document_ids() -> None:
    fragments = [
        Fragment(text="...", provenance="retrieved_verified", doc_id="d001"),
        Fragment(text="...", provenance="retrieved_verified", doc_id="d020"),
    ]
    assert build_citation(_decision(), fragments) == "(d001, d020)"


def test_no_citation_when_there_is_nothing_to_cite() -> None:
    assert build_citation(_decision(), []) == ""


def test_a_citation_is_not_duplicated() -> None:
    hint = RepairHint(span=(0, 5), unsupported_claim="x", evidence=["Clause 9.1 states ..."])
    once = Annotator().annotate("No charge applies.", _decision(hint=hint))
    twice = Annotator().annotate(once, _decision(hint=hint))
    assert twice.count("(Clause 9.1)") == 1


# --- hedge softening ------------------------------------------------------- #


def test_overconfidence_is_softened() -> None:
    """'Confidence is the instruction humans follow' -- so the transform targets how
    certain the sentence sounds."""
    result = Annotator().annotate(
        "You are entitled to a full refund.", _decision(probs={"overconfident": 0.7})
    )
    assert "may be entitled" in result


def test_softening_does_not_change_what_is_claimed() -> None:
    """Rewriting the claim is L2's job and needs evidence. Quietly editing facts under
    the banner of 'annotation' would be the most dangerous thing in the module."""
    result = Annotator().soften("The penalty is 2% of the outstanding principal.")
    assert "2%" in result
    assert "outstanding principal" in result


def test_longer_phrases_are_matched_first() -> None:
    """Otherwise 'will always' becomes 'will normally normally'."""
    assert Annotator().soften("We will always honour it.") == "We should normally honour it."


def test_softening_respects_word_boundaries() -> None:
    assert "alwaysonline" in Annotator().soften("The alwaysonline service is up.")


def test_softening_only_applies_when_overconfidence_was_detected() -> None:
    sentence = "You are entitled to a full refund."
    assert Annotator().annotate(sentence, _decision(probs={"ungrounded": 0.0})) == sentence


def test_annotation_is_deterministic() -> None:
    """Same inputs, same output -- which is what lets a decision be replayed (F9)."""
    hint = RepairHint(span=(0, 5), unsupported_claim="x", evidence=["Clause 9.1 ..."])
    decision = _decision(probs={"ungrounded": 0.5, "overconfident": 0.5}, hint=hint)
    annotator = Annotator()
    sentence = "You are entitled to a full refund."
    assert annotator.annotate(sentence, decision) == annotator.annotate(sentence, decision)


def test_annotation_never_empties_a_sentence() -> None:
    result = Annotator().annotate("Yes.", _decision(probs={"ungrounded": 0.99}))
    assert result.strip()
    assert "Yes" in result


def test_an_empty_sentence_is_returned_unchanged() -> None:
    assert Annotator().annotate("   ", _decision()) == "   "


# =========================================================================== #
# L2 -- repair
# =========================================================================== #


class FakeProvider:
    """Returns scripted completions and records what it was asked."""

    def __init__(self, replies: list[str | None]):
        self.replies = replies
        self.bodies: list[dict[str, Any]] = []

    async def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        reply = self.replies[min(len(self.bodies) - 1, len(self.replies) - 1)]
        if reply is None:
            raise RuntimeError("upstream failed")
        return {"choices": [{"message": {"role": "assistant", "content": reply}}]}


class VerifyingEngine:
    """Accepts or rejects replacements on a script."""

    def __init__(self, verdicts: list[str]):
        self.verdicts = verdicts
        self.calls = 0

    async def evaluate(self, ctx: Any) -> Decision:
        verdict = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        return _decision(action=verdict)

    async def prefetch(self, *a: Any, **k: Any) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {}


def _repairer(provider: Any, engine: Any) -> SentenceRepairer:
    return SentenceRepairer(
        provider=provider,
        model="qwen3:4b",
        risk_engine=engine,
        stakes=_stakes(),
        request_id="req_1",
        question="Does prepaying my home loan attract a penalty?",
        retrieved=[
            Fragment(
                text="Clause 9.1 states no prepayment charge applies.",
                provenance="retrieved_verified",
                doc_id="d001",
            )
        ],
    )


async def test_a_verified_replacement_is_returned() -> None:
    repairer = _repairer(
        FakeProvider(["Clause 9.1 applies, so no prepayment charge is payable."]),
        VerifyingEngine(["L0_pass"]),
    )
    result = await repairer.repair(
        "Clause 7.4 imposes a 2% penalty.", _decision(action="L2_repair"), ""
    )
    assert result.verified is True
    assert "Clause 9.1" in (result.text or "")
    assert result.attempts == 1


async def test_an_unverified_replacement_is_retried_then_abandoned() -> None:
    """A repair nobody could verify is just a different unverified sentence, and a
    third attempt costs more than rerouting."""
    engine = VerifyingEngine(["L4_hold", "L4_hold"])
    repairer = _repairer(FakeProvider(["Still wrong.", "Still wrong again."]), engine)
    result = await repairer.repair("Bad sentence.", _decision(action="L2_repair"), "")
    assert result.text is None
    assert result.attempts == 2
    assert engine.calls == 2


async def test_the_second_attempt_can_succeed() -> None:
    repairer = _repairer(
        FakeProvider(["Wrong.", "Clause 9.1 applies."]),
        VerifyingEngine(["L4_hold", "L0_pass"]),
    )
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.verified is True
    assert result.attempts == 2


async def test_an_upstream_failure_does_not_raise() -> None:
    """A failed repair escalates. It never 500s the customer's request."""
    repairer = _repairer(FakeProvider([None]), VerifyingEngine(["L0_pass"]))
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.text is None


async def test_the_repair_uses_the_same_model_not_a_stronger_one() -> None:
    """Escalating the tier is L3 and is priced separately. Silently upgrading here would
    make a repair cost what a reroute costs while still being labelled a repair."""
    provider = FakeProvider(["Fixed."])
    repairer = _repairer(provider, VerifyingEngine(["L0_pass"]))
    await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert provider.bodies[0]["model"] == "qwen3:4b"


async def test_the_repair_is_bounded() -> None:
    """An unbounded repair wanders into a second paragraph, and then the gate is holding
    something that is no longer one sentence."""
    provider = FakeProvider(["Fixed."])
    await _repairer(provider, VerifyingEngine(["L0_pass"])).repair(
        "Bad.", _decision(action="L2_repair"), ""
    )
    body = provider.bodies[0]
    # Bounded, but with headroom for the reasoning block the model emits regardless.
    assert body["max_tokens"] <= 80 + 96
    # No newline stop: verified against a live qwen3, it fires on the first newline
    # INSIDE the <think> block, so the completion is the literal string '<think>'
    # and every repair silently fails.
    assert "stop" not in body
    assert body["temperature"] == 0.0  # deterministic, so the repair is replayable


async def test_a_multi_line_reply_is_reduced_to_one_sentence() -> None:
    """`stop` should handle this, but not every provider honours it."""
    repairer = _repairer(
        FakeProvider(["Clause 9.1 applies.\nAlso, here is more waffle."]),
        VerifyingEngine(["L0_pass"]),
    )
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.text == "Clause 9.1 applies."


@pytest.mark.parametrize("prefix", ["Corrected sentence:", "Answer:", "Correction:"])
async def test_model_preamble_is_stripped(prefix: str) -> None:
    repairer = _repairer(
        FakeProvider([f'{prefix} "Clause 9.1 applies."']), VerifyingEngine(["L0_pass"])
    )
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.text == "Clause 9.1 applies."


async def test_the_gate_callback_shape_works() -> None:
    repairer = _repairer(FakeProvider(["Clause 9.1 applies."]), VerifyingEngine(["L0_pass"]))
    assert await repairer("Bad.", _decision(action="L2_repair"), "") == "Clause 9.1 applies."
    assert repairer.last_result is not None
    assert repairer.last_result.verified is True


# --- the prompt itself ----------------------------------------------------- #


def test_the_prompt_carries_the_evidence_and_the_claim() -> None:
    messages = build_repair_messages(
        sentence="Clause 7.4 imposes a 2% penalty.",
        question="Is there a penalty?",
        answer_prefix="Under your agreement, ",
        evidence=["Clause 9.1 states no prepayment charge applies."],
        unsupported_claim="Clause 7.4 imposes a 2% penalty",
    )
    user = messages[1]["content"]
    assert "Clause 9.1 states no prepayment charge applies." in user
    assert "Clause 7.4 imposes a 2% penalty" in user
    assert "Is there a penalty?" in user


def test_the_prompt_includes_the_answer_prefix() -> None:
    """A repair that is individually correct but reads as a non sequitur has produced a
    worse answer than the one it replaced."""
    messages = build_repair_messages(
        sentence="x",
        question="q",
        answer_prefix="Under your agreement, ",
        evidence=["e"],
    )
    assert "Under your agreement," in messages[1]["content"]


def test_the_prompt_says_so_when_nothing_was_retrieved() -> None:
    """The model must not be left to infer that silence means agreement."""
    messages = build_repair_messages(sentence="x", question="q", answer_prefix="", evidence=[])
    assert "no supporting passage was retrieved" in messages[1]["content"]


def test_the_system_prompt_forbids_preamble_and_requires_a_citation() -> None:
    messages = build_repair_messages(sentence="x", question="q", answer_prefix="", evidence=["e"])
    system = messages[0]["content"].lower()
    assert "nothing else" in system
    assert "cite" in system


# --------------------------------------------------------------------------- #
# Reasoning blocks in a repair completion (F-004, second occurrence)
# --------------------------------------------------------------------------- #


async def test_a_reasoning_block_is_not_shipped_as_the_repair() -> None:
    """Found live against qwen3, and it is the worst kind of bug: silent and shipped.

    A non-streaming completion returns the reasoning inline, so "the first line" of the
    response is the literal string "<think>". The customer received a reasoning tag as
    their corrected sentence, and every test passed because the fixtures were clean.
    """
    repairer = _repairer(
        FakeProvider(["<think>\n\n</think>\n\nThe annual fee is Rs. 500 plus taxes."]),
        VerifyingEngine(["L0_pass"]),
    )
    result = await repairer.repair("The fee is Rs. 5,000.", _decision(action="L2_repair"), "")
    assert result.text == "The annual fee is Rs. 500 plus taxes."
    assert "think" not in (result.text or "")


async def test_inline_reasoning_is_removed() -> None:
    repairer = _repairer(
        FakeProvider(["<think>I should check clause 9.1</think>Clause 9.1 applies."]),
        VerifyingEngine(["L0_pass"]),
    )
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.text == "Clause 9.1 applies."


async def test_a_truncated_reasoning_block_yields_no_repair() -> None:
    """An opener with no closer means the completion was cut off mid-reasoning. There is
    no answer in it, and escalating is better than shipping half a thought."""
    repairer = _repairer(
        FakeProvider(["<think>the customer is asking about"]), VerifyingEngine(["L0_pass"])
    )
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.text is None


@pytest.mark.parametrize("tag", ["think", "thinking", "reasoning"])
async def test_every_known_reasoning_tag_is_handled(tag: str) -> None:
    repairer = _repairer(
        FakeProvider([f"<{tag}>musing</{tag}>Clause 9.1 applies."]),
        VerifyingEngine(["L0_pass"]),
    )
    result = await repairer.repair("Bad.", _decision(action="L2_repair"), "")
    assert result.text == "Clause 9.1 applies."
