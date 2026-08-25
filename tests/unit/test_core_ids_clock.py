"""Identifiers, digests and deadlines.

Small surface, but three things downstream depend on it being exactly right: replay
(F9) needs a stable digest, the observer's KV cache needs a content-addressed key, and
the latency term of the objective needs an honest clock.
"""

from __future__ import annotations

import time

import pytest

from interlock.core.clock import Deadline, monotonic_ms
from interlock.core.ids import (
    context_key,
    inputs_digest,
    new_decision_id,
    new_request_id,
    new_stakes_id,
)
from interlock.core.types import Fragment

# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #


def test_ids_carry_their_type_as_a_prefix() -> None:
    """A stray id in a log or a trace should be self-describing."""
    assert new_request_id().startswith("req_")
    assert new_decision_id().startswith("dec_")
    assert new_stakes_id().startswith("stk_")


def test_ids_are_unique() -> None:
    assert len({new_request_id() for _ in range(2000)}) == 2000


def test_ids_sort_by_creation_time() -> None:
    """So ORDER BY on an id column does the obvious thing."""
    first = new_request_id()
    time.sleep(0.002)
    second = new_request_id()
    assert first < second


# --------------------------------------------------------------------------- #
# Digests -- replayability (F9)
# --------------------------------------------------------------------------- #


def test_digest_is_stable_across_key_order() -> None:
    """A digest must not change because a dict happened to be built differently."""
    assert inputs_digest({"a": 1, "b": 2}) == inputs_digest({"b": 2, "a": 1})


def test_digest_changes_when_an_input_changes() -> None:
    """Otherwise replay cannot detect that the stored inputs were not what we recorded."""
    assert inputs_digest({"sentence": "Clause 7.4"}) != inputs_digest({"sentence": "Clause 9.1"})


def test_digest_is_labelled_with_its_algorithm() -> None:
    assert inputs_digest({"a": 1}).startswith("sha256:")


# --------------------------------------------------------------------------- #
# Context key -- the KV-prefix cache
# --------------------------------------------------------------------------- #


def _fragments() -> list[Fragment]:
    return [
        Fragment(text="Clause 9.1 ...", provenance="retrieved_verified", doc_id="d17"),
        Fragment(text="Fee schedule ...", provenance="retrieved_verified", doc_id="d02"),
    ]


def test_context_key_is_content_addressed() -> None:
    """Two requests that retrieved the same context share a warm prefix -- which is the
    difference between ~200 ms and ~12 ms per sentence."""
    assert context_key(_fragments()) == context_key(_fragments())


def test_context_key_changes_when_the_context_changes() -> None:
    other = [*_fragments(), Fragment(text="new chunk", provenance="retrieved_verified")]
    assert context_key(_fragments()) != context_key(other)


def test_context_key_changes_when_provenance_changes() -> None:
    """A fragment whose trust level changed is a different fragment to the probe -- and
    to the tool interlock that reads the same labels."""
    trusted = [Fragment(text="same text", provenance="retrieved_verified")]
    untrusted = [Fragment(text="same text", provenance="retrieved_untrusted")]
    assert context_key(trusted) != context_key(untrusted)


def test_context_key_is_order_sensitive() -> None:
    """Prefix caching is positional: reordering the context invalidates the prefix."""
    forward = _fragments()
    assert context_key(forward) != context_key(list(reversed(forward)))


def test_context_key_accepts_fragments_dicts_and_strings() -> None:
    """It is computed in the gateway, the observer and the eval harness, which do not
    all hold the same representation."""
    as_model = [Fragment(text="hello", provenance="user")]
    as_dict = [{"text": "hello", "provenance": "user"}]
    assert context_key(as_model) == context_key(as_dict)


def test_context_key_survives_an_empty_fragment() -> None:
    """An empty retrieved chunk is unusual but not an error, and must not raise."""
    assert context_key([Fragment(text="", provenance="retrieved_verified")]).startswith("sha256:")


def test_context_key_of_no_context_is_stable() -> None:
    assert context_key([]) == context_key([])


# --------------------------------------------------------------------------- #
# Deadlines -- latency is part of the objective, not a side constraint
# --------------------------------------------------------------------------- #


def test_deadline_counts_down() -> None:
    deadline = Deadline(budget_ms=50)
    assert deadline.remaining_ms <= 50
    assert not deadline.expired


def test_deadline_expires() -> None:
    deadline = Deadline(budget_ms=0)
    assert deadline.expired


def test_overrun_is_reported_honestly_as_negative() -> None:
    """A clamped zero hides exactly the regression you are hunting: a caller that
    overran needs to know by how much."""
    deadline = Deadline(budget_ms=5, started_ms=monotonic_ms() - 100.0)
    assert deadline.remaining_ms < 0
    assert deadline.elapsed_ms >= 100.0


def test_remaining_seconds_is_clamped_for_wait_for() -> None:
    """asyncio.wait_for rejects a negative timeout, so this is the one place clamping is
    correct -- and it is kept separate from remaining_ms so measurement stays honest."""
    deadline = Deadline(budget_ms=5, started_ms=monotonic_ms() - 100.0)
    assert deadline.remaining_seconds() == 0.0
    assert deadline.remaining_ms < 0


def test_a_child_deadline_never_outlives_its_parent() -> None:
    """Lane B must not be able to hand the observer a budget the request no longer has."""
    parent = Deadline(budget_ms=40)
    child = parent.child(budget_ms=1000)
    assert child.budget_ms <= 40


def test_a_child_deadline_can_be_tighter_than_its_parent() -> None:
    parent = Deadline(budget_ms=1000)
    assert parent.child(budget_ms=25).budget_ms == pytest.approx(25)


def test_a_child_of_an_expired_parent_has_no_budget() -> None:
    expired = Deadline(budget_ms=5, started_ms=monotonic_ms() - 100.0)
    assert expired.child(budget_ms=50).budget_ms == 0.0
