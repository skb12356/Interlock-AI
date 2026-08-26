"""Latency attribution.

The headline claim is "≤ 120 ms added p95, time-to-first-token statistically unchanged".
These tests are about the ways that claim can be supported by a number that does not mean
what it says: counting concurrent work as if the customer waited for it, pooling two
populations with different profiles, or quoting a percentile from six samples.
"""

from __future__ import annotations

import pytest

from interlock.gateway.latency import (
    MIN_SAMPLES_FOR_PERCENTILE,
    LaneTimer,
    LatencyRecorder,
    LatencySample,
    percentile,
)


def _sample(overhead: float, **kwargs: object) -> LatencySample:
    payload: dict = {
        "request_id": "r",
        "overhead_ms": overhead,
        "ttft_ms": 300.0,
        "by_lane": {"lane_a": overhead},
        "buffered": False,
        "tier": "cheap",
    }
    payload.update(kwargs)
    return LatencySample(**payload)


# --------------------------------------------------------------------------- #
# Percentiles
# --------------------------------------------------------------------------- #


def test_the_percentile_is_a_value_that_actually_occurred() -> None:
    """Nearest-rank, not interpolated. An interpolated p95 reports a latency no request
    experienced, which is awkward when somebody asks which request that was."""
    values = [1.0, 2.0, 3.0, 100.0]
    assert percentile(values, 0.95) in values


def test_an_empty_sample_is_zero_not_an_error() -> None:
    assert percentile([], 0.95) == 0.0


def test_a_thin_sample_refuses_to_call_itself_a_p95() -> None:
    """A percentile over six observations describes the sample, not the distribution."""
    recorder = LatencyRecorder()
    for _ in range(6):
        recorder.record(_sample(10.0))
    report = recorder.report()
    assert report["within_budget"] is None
    assert any("must not be reported as a p95" in note for note in report["notes"])


def test_a_full_sample_does_give_a_verdict() -> None:
    recorder = LatencyRecorder()
    for _ in range(MIN_SAMPLES_FOR_PERCENTILE + 10):
        recorder.record(_sample(15.0))
    report = recorder.report(budget_ms=120.0)
    assert report["within_budget"] is True
    assert report["overhead_p95_ms"] == 15.0


def test_exceeding_the_budget_is_stated_not_left_to_be_noticed() -> None:
    recorder = LatencyRecorder()
    for _ in range(50):
        recorder.record(_sample(400.0))
    report = recorder.report(budget_ms=120.0)
    assert report["within_budget"] is False
    assert any("exceeds the 120 ms budget" in note for note in report["notes"])


# --------------------------------------------------------------------------- #
# The distinctions that stop the number lying
# --------------------------------------------------------------------------- #


def test_buffered_and_unbuffered_are_reported_separately() -> None:
    """Pooling them produces a p95 that describes neither. Buffered traffic is the
    minority by design, so a pooled figure mostly reflects the unbuffered path while
    the SLA risk lives in the other one."""
    recorder = LatencyRecorder()
    for _ in range(60):
        recorder.record(_sample(10.0, buffered=False))
    for _ in range(10):
        recorder.record(_sample(900.0, buffered=True))
    report = recorder.report()
    assert report["unbuffered"]["p95_ms"] == 10.0
    assert report["buffered"]["p95_ms"] == 900.0
    assert report["unbuffered"]["n"] == 60
    assert report["buffered"]["n"] == 10


def test_ttft_is_measured_separately_from_total_overhead() -> None:
    """They answer different questions -- responsiveness versus the SLA -- and a system
    can be excellent at one and poor at the other."""
    recorder = LatencyRecorder()
    for _ in range(40):
        recorder.record(_sample(12.0, ttft_ms=250.0))
    report = recorder.report()
    assert report["overhead_p95_ms"] == 12.0
    assert report["ttft_p95_ms"] == 250.0


def test_unattributed_overhead_is_reported_not_folded_into_a_lane() -> None:
    """A large remainder means something is spending time nobody instrumented. Folding
    it into the nearest lane would hide exactly the thing worth finding."""
    recorder = LatencyRecorder()
    for _ in range(40):
        recorder.record(_sample(100.0, by_lane={"lane_a": 20.0}))
    report = recorder.report()
    assert report["unattributed_mean_ms"] == pytest.approx(80.0)
    assert any("unattributed" in note for note in report["notes"])


def test_fully_attributed_overhead_raises_no_note() -> None:
    recorder = LatencyRecorder()
    for _ in range(40):
        recorder.record(_sample(30.0, by_lane={"lane_a": 20.0, "gate_hold": 10.0}))
    report = recorder.report()
    assert report["unattributed_mean_ms"] == 0.0
    assert not any("unattributed" in note for note in report["notes"])


def test_concurrent_lane_b_work_is_not_counted_as_overhead() -> None:
    """Lane B runs alongside generation. Counting its wall-clock would make a
    correctly-designed system look slow; only the part the gate actually waited on is
    latency the customer experienced."""
    from interlock.gateway.latency import LANES

    assert "lane_b" not in LANES
    assert "gate_hold" in LANES


def test_per_lane_p95_identifies_the_expensive_lane() -> None:
    """"The guardrail is slow" is not actionable. "The repair lane is the p95" is."""
    recorder = LatencyRecorder()
    for _ in range(40):
        recorder.record(
            _sample(300.0, by_lane={"lane_a": 12.0, "repair": 280.0, "ledger": 8.0})
        )
    lanes = recorder.report()["by_lane_p95_ms"]
    assert lanes["repair"] == 280.0
    assert max(lanes, key=lambda k: lanes[k]) == "repair"


# --------------------------------------------------------------------------- #
# Bookkeeping
# --------------------------------------------------------------------------- #


def test_the_window_is_bounded() -> None:
    """This lives in a long-running process; an unbounded list of every request's
    timing is a memory leak with a graph attached."""
    recorder = LatencyRecorder(window=50)
    for _ in range(500):
        recorder.record(_sample(10.0))
    assert len(recorder.samples) == 50


def test_the_lane_timer_accumulates_repeated_work() -> None:
    """A request can repair twice. The second must not overwrite the first."""
    timer = LaneTimer()
    timer.add("repair", 100.0)
    timer.add("repair", 150.0)
    timer.add("lane_a", 12.0)
    assert timer.snapshot() == {"repair": 250.0, "lane_a": 12.0}


def test_zero_and_negative_timings_are_ignored() -> None:
    """A clock that went backwards should not show up as a lane that ran."""
    timer = LaneTimer()
    timer.add("repair", 0.0)
    timer.add("lane_a", -5.0)
    assert timer.snapshot() == {}
