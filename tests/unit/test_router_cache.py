"""Router and semantic cache.

Both spend money on Interlock's behalf, and both fail in the same direction if they are
wrong: cheaply and confidently. The cache especially — a near-miss produces a fluent
answer to a question nobody asked, and there is no generation step left in which anything
could notice.

So the cache tests are mostly about the four conditions being genuinely conjunctive.
Any three of them still admit the fourth's failure, and the one people leave out is the
context hash: it is what stops the system serving last month's answer after a clause was
superseded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interlock.core.policy import load_policy
from interlock.core.types import Fragment, Stakes
from interlock.gateway.cache import (
    SIMILARITY_THRESHOLD,
    SemanticCache,
    context_hash,
    cosine,
)
from interlock.gateway.router import HeuristicDifficulty, Router

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(REPO_ROOT / "policies" / "banking.yaml")

CLEAN = Fragment(
    text="Clause 9.1: no prepayment charge applies.",
    provenance="retrieved_verified",
    doc_id="d001#0",
    domain="prepayment",
)


def _stakes(impact: float, domain: str = "general") -> Stakes:
    return Stakes(
        impact_inr=impact,
        reversibility="costly" if impact >= 1000 else "reversible",
        domain=domain,
        confidence=0.9,
    )


# --------------------------------------------------------------------------- #
# Router: stakes dominates
# --------------------------------------------------------------------------- #


def test_high_stakes_forces_the_strong_tier_however_easy_the_question() -> None:
    """A router able to talk itself out of the strong model on a high-stakes question
    has broken the guarantee the stakes estimate exists to provide."""
    router = Router(policy=POLICY)
    decision = router.route(
        stakes=_stakes(40_000, "prepayment"), question="Fee?", retrieved=[CLEAN]
    )
    assert decision.tier == "strong"
    assert decision.forced_by_stakes
    assert decision.reason == "stakes_high"


def test_low_stakes_easy_questions_go_cheap() -> None:
    """Where the saving comes from. If this stops happening, the router is decoration."""
    router = Router(policy=POLICY)
    decision = router.route(
        stakes=_stakes(50, "branch_info"),
        question="What are the branch timings?",
        retrieved=[CLEAN],
    )
    assert decision.tier == "cheap"
    assert not decision.forced_by_stakes


def test_a_hard_question_is_upgraded_even_at_low_stakes() -> None:
    """A repair costs ~14 s and a second generation, so upgrading up front is cheaper
    than discovering the problem at the commit gate."""
    router = Router(policy=POLICY)
    decision = router.route(
        stakes=_stakes(50, "branch_info"),
        question="Compare the fees on a savings account versus a current account, and "
        "also calculate the total interest if I hold Rs. 200000 for two years.",
        retrieved=[CLEAN],
    )
    assert decision.tier == "strong"
    assert decision.difficulty >= 0.65


def test_the_route_reason_does_not_claim_a_model_we_do_not_have() -> None:
    """The plan names RouteLLM's 'mf' controller. This build ships a deterministic
    stand-in, and labelling it 'router_mf' would claim a trained matrix factorisation
    that does not exist."""
    router = Router(policy=POLICY)
    decision = router.route(
        stakes=_stakes(50),
        question="Compare account A versus account B and calculate the total interest.",
        retrieved=[CLEAN],
    )
    assert decision.reason != "router_mf"
    assert "heuristic" in decision.reason


def test_the_stakes_id_travels_with_the_route() -> None:
    """Contribution 1 must be provable from ONE trace: the router and the risk engine
    carrying the same estimate id is what makes that possible."""
    router = Router(policy=POLICY)
    decision = router.route(
        stakes=_stakes(50), question="Timings?", retrieved=[CLEAN], stakes_id="stk_abc"
    )
    assert decision.stakes_id == "stk_abc"
    assert decision.as_event()["stakes_id"] == "stk_abc"


def test_no_retrieval_is_the_hardest_case() -> None:
    """With nothing retrieved the model answers from parameters, which is where a small
    model is weakest and most confident."""
    assert HeuristicDifficulty().score("What is the fee?", []) == 1.0


def test_difficulty_takes_the_max_not_the_mean() -> None:
    """Difficulty is a bottleneck, not an average. One genuinely hard aspect makes the
    whole question hard, and averaging it against three easy ones would route it cheap."""
    model = HeuristicDifficulty()
    easy = model.score("What is the fee?", [CLEAN])
    hard = model.score("What is the fee? Compare it versus the premium account.", [CLEAN])
    assert hard > easy
    assert hard >= 0.9


# --------------------------------------------------------------------------- #
# Cache: the four conditions, and that they are conjunctive
# --------------------------------------------------------------------------- #


def _cache(**kwargs: object) -> SemanticCache:
    return SemanticCache(policy_version="banking-v3@test", **kwargs)  # type: ignore[arg-type]


def _stored(cache: SemanticCache, embedding: list[float], **overrides: object) -> bool:
    payload: dict = {
        "question": "What is the annual fee?",
        "answer": "The annual fee is Rs. 500.",
        "embedding": embedding,
        "retrieved": [CLEAN],
        "stakes_inr": 200.0,
        "action": "L0_pass",
        "model": "qwen3:4b",
    }
    payload.update(overrides)
    return cache.store(**payload)


def test_an_identical_question_with_identical_context_hits() -> None:
    cache = _cache()
    assert _stored(cache, [1.0, 0.0, 0.0])
    result = cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[CLEAN],
        stakes_inr=200.0,
    )
    assert result.hit
    assert result.similarity == pytest.approx(1.0)
    assert result.entry is not None and "Rs. 500" in result.entry.answer


def test_a_merely_similar_question_misses() -> None:
    """ "What is the fee" and "what is the fee for premium accounts" must not collide.
    A threshold that admitted that would answer the second with the first, confidently."""
    cache = _cache()
    assert _stored(cache, [1.0, 0.0, 0.0])
    result = cache.lookup(
        question="What is the fee for premium accounts?",
        embedding=[0.8, 0.6, 0.0],
        retrieved=[CLEAN],
        stakes_inr=200.0,
    )
    assert not result.hit
    assert cosine([1.0, 0.0, 0.0], [0.8, 0.6, 0.0]) < SIMILARITY_THRESHOLD


def test_identical_last_question_cannot_cross_full_prompt_scopes() -> None:
    """Prior/system messages may contain customer secrets even when the final question is generic."""
    cache = _cache()
    assert _stored(cache, [1.0, 0.0, 0.0], scope_digest="customer-a-full-prompt")
    result = cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[CLEAN],
        stakes_inr=200.0,
        scope_digest="customer-b-full-prompt",
    )
    assert not result.hit
    assert "prompt scope" in result.reason


def test_a_changed_context_misses_and_says_why() -> None:
    """THE condition people leave out. A clause is superseded, a rate card expires -- an
    answer correct against last month's context is wrong now and looks perfectly
    plausible."""
    cache = _cache()
    assert _stored(cache, [1.0, 0.0, 0.0])
    superseded = Fragment(
        text="Clause 7.4: a 2% foreclosure charge applies.",
        provenance="retrieved_verified",
        doc_id="d001#0",  # same doc id, different content -- the re-upload case
        domain="prepayment",
    )
    result = cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[superseded],
        stakes_inr=200.0,
    )
    assert not result.hit
    assert "superseded or re-uploaded" in result.reason


def test_the_context_hash_covers_text_not_just_doc_id() -> None:
    """A document can be re-uploaded under the same identifier with different contents,
    which is exactly the supersession case."""
    original = Fragment(text="A", provenance="retrieved_verified", doc_id="d001#0")
    edited = Fragment(text="B", provenance="retrieved_verified", doc_id="d001#0")
    assert context_hash([original]) != context_hash([edited])


def test_retrieval_order_does_not_change_the_hash() -> None:
    """The same passages in a different order is the same context, and treating it as a
    miss would throw away most legitimate hits."""
    a = Fragment(text="A", provenance="retrieved_verified", doc_id="d1")
    b = Fragment(text="B", provenance="retrieved_verified", doc_id="d2")
    assert context_hash([a, b]) == context_hash([b, a])


def test_high_stakes_never_hits() -> None:
    """A Rs.40,000 answer is regenerated and re-verified every time; the saving is not
    worth the chance that something moved."""
    cache = _cache()
    _stored(cache, [1.0, 0.0, 0.0], stakes_inr=200.0)
    result = cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[CLEAN],
        stakes_inr=40_000.0,
    )
    assert not result.hit
    assert "cache ceiling" in result.reason


@pytest.mark.parametrize(
    "action", ["L1_annotate", "L2_repair", "L3_reroute", "L4_hold", "L5_block"]
)
def test_only_a_clean_pass_is_ever_stored(action: str) -> None:
    """Caching a repaired or held answer replays the defect on every subsequent hit --
    turning one bad answer into a permanent one, at machine speed."""
    cache = _cache()
    assert _stored(cache, [1.0, 0.0, 0.0], action=action) is False
    assert len(cache) == 0


def test_a_high_stakes_answer_is_never_stored_either() -> None:
    cache = _cache()
    assert _stored(cache, [1.0, 0.0, 0.0], stakes_inr=40_000.0) is False


def test_a_policy_change_invalidates_the_cache() -> None:
    """The policy is what decided the cached answer was acceptable in the first place."""
    cache = _cache()
    _stored(cache, [1.0, 0.0, 0.0])
    cache.policy_version = "banking-v4@different"
    result = cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[CLEAN],
        stakes_inr=200.0,
    )
    assert not result.hit


def test_the_conditions_are_conjunctive() -> None:
    """Any three still admit the fourth's failure, so all four are checked every time."""
    cache = _cache()
    _stored(cache, [1.0, 0.0, 0.0])
    # Similarity + policy + action all fine; only the context differs.
    other = Fragment(text="different", provenance="retrieved_verified", doc_id="d999")
    assert not cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[other],
        stakes_inr=200.0,
    ).hit
    # Similarity + context + action fine; only the stakes differ.
    assert not cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[CLEAN],
        stakes_inr=99_999.0,
    ).hit


def test_a_miss_reports_why() -> None:
    """ "Cache miss" tells an operator nothing. "Missed because the context hash changed"
    tells them a document was re-uploaded."""
    cache = _cache()
    cache.lookup(question="q", embedding=[1.0], retrieved=[CLEAN], stakes_inr=40_000.0)
    assert any("ceiling" in reason for reason in cache.stats()["miss_reasons"])


def test_the_cache_is_bounded() -> None:
    """An in-process dict with no ceiling grows the gateway's memory instead (no Redis,
    by design)."""
    cache = _cache(capacity=10)
    for index in range(50):
        _stored(cache, [1.0, float(index), 0.0], question=f"q{index}")
    assert len(cache) == 10


def test_stats_report_the_hit_rate_and_the_caveat() -> None:
    cache = _cache()
    _stored(cache, [1.0, 0.0, 0.0])
    cache.lookup(
        question="What is the annual fee?",
        embedding=[1.0, 0.0, 0.0],
        retrieved=[CLEAN],
        stakes_inr=200.0,
    )
    cache.lookup(question="x", embedding=[0.0, 1.0, 0.0], retrieved=[CLEAN], stakes_inr=200.0)
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert "conjunctive" in stats["note"]


def test_cosine_handles_degenerate_vectors() -> None:
    assert cosine([], []) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0, "mismatched widths are not comparable"


# --------------------------------------------------------------------------- #
# Two routing bugs, both of which sent ALL traffic to the strong tier
# --------------------------------------------------------------------------- #


def test_retrieval_that_never_ran_is_not_evidence_of_difficulty() -> None:
    """ "Found nothing" and "never looked" are opposite facts.

    A deployment where the caller does its own RAG attaches no context, and scoring
    that as maximally hard would route EVERY request to the strong tier in exactly the
    shape the proxy is designed for -- silently destroying the routing saving.
    """
    model = HeuristicDifficulty()
    assert model.score("What is the fee?", [], retrieval_attempted=True) == 1.0
    assert model.score("What is the fee?", [], retrieval_attempted=False) < 0.2


def test_the_number_of_retrieved_documents_is_not_a_difficulty_signal() -> None:
    """Retrieval always returns k passages, so counting them measures the retriever's k
    and nothing about the question. An earlier version did, scored 1.0 on every
    retrieved request, and routed everything strong."""
    model = HeuristicDifficulty()
    one = [Fragment(text="a", provenance="retrieved_verified", doc_id="d1#0", score=0.9)]
    four = [
        Fragment(text=str(i), provenance="retrieved_verified", doc_id=f"d{i}#0", score=0.9)
        for i in range(4)
    ]
    assert model.score("What is the fee?", one) == model.score("What is the fee?", four)


def test_rrf_scores_do_not_move_the_difficulty() -> None:
    """RRF is a sum of 1/(60 + rank): it carries RANK and deliberately discards
    magnitude, so 0.02433 vs 0.02334 across four hits says nothing about retrieval
    quality. A spread measure built on those magnitudes read the fusion constant and
    scored 0.90 on a branch-hours question.
    """
    model = HeuristicDifficulty()
    decisive = [
        Fragment(text=str(i), provenance="retrieved_verified", doc_id=f"d{i}", score=s)
        for i, s in enumerate([0.99, 0.10, 0.09, 0.08])
    ]
    flat = [
        Fragment(text=str(i), provenance="retrieved_verified", doc_id=f"d{i}", score=s)
        for i, s in enumerate([0.02433, 0.02382, 0.02344, 0.02334])
    ]
    question = "What time does the branch open?"
    assert model.score(question, decisive) == model.score(question, flat)


def test_an_easy_low_stakes_question_stays_cheap_end_to_end() -> None:
    """The regression both bugs produced, asserted at the routing level."""
    router = Router(policy=POLICY)
    retrieved = [
        Fragment(text=str(i), provenance="retrieved_verified", doc_id=f"d{i}#0", score=0.024)
        for i in range(4)
    ]
    decision = router.route(
        stakes=_stakes(50, "branch_info"),
        question="What time does the branch open?",
        retrieved=retrieved,
    )
    assert decision.tier == "cheap", decision
