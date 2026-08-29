"""The ledger — Contract 5.

Two properties under test, and they pull in opposite directions on purpose:

* telemetry must **never** block the token path, so it is fire-and-forget and lossy
  under pressure — but it must *count* what it loses;
* a hold must **never** be lost, so it is awaited and committed before the caller
  proceeds.

Getting these the wrong way round would either stall streams on a slow disk or lose the
review card that the whole tool-interlock feature exists to guarantee.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from interlock.core.types import Decision, LossRow, SignalReading
from interlock.ledger.writer import (
    Ledger,
    RequestBatch,
    SpanEntry,
    SpendEntry,
    apply_migrations,
    connect,
)


@pytest.fixture
async def ledger(tmp_path: Path) -> AsyncIterator[Ledger]:
    instance = Ledger(db_path=tmp_path / "test.db")
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


def _batch(request_id: str = "req_1", **kwargs: object) -> RequestBatch:
    base: dict[str, object] = {
        "request_id": request_id,
        "trace_id": "trc_1",
        "tenant_id": "demo",
        "model_served": "qwen3:4b",
        "route_reason": "stakes_low",
        "stakes_id": "stk_1",
        "stakes_impact_inr": 200.0,
        "stakes_domain": "general",
    }
    base.update(kwargs)
    return RequestBatch(**base)  # type: ignore[arg-type]


def _decision(decision_id: str = "dec_1") -> Decision:
    return Decision(
        decision_id=decision_id,
        action="L2_repair",
        loss_table=[
            LossRow(
                action="L0_pass",
                residual_harm=1.0,
                nuisance=0.0,
                compute=0.0,
                latency=0.0,
                total=1.0,
            )
        ],
        chosen_loss=1.0,
        probs={"ungrounded": 0.31},
        why=["because"],
        policy_version="banking-v3@sha256:abc",
        inputs_digest="sha256:abc",
    )


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #


def test_migrations_create_every_table(tmp_path: Path) -> None:
    connection = connect(tmp_path / "m.db")
    apply_migrations(connection)
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for expected in (
        "requests",
        "signals",
        "decisions",
        "spend",
        "tool_calls",
        "holds",
        "rework_edges",
        "shadow_runs",
        "fairness_pairs",
        "labels",
        "spans",
    ):
        assert expected in tables


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """Applied at boot, so re-running must be a no-op rather than an error."""
    connection = connect(tmp_path / "m.db")
    first = apply_migrations(connection)
    second = apply_migrations(connection)
    assert first == ["001_initial"]
    assert second == []


def test_wal_mode_is_enabled(tmp_path: Path) -> None:
    """WAL is what lets DuckDB read while we write (ADR-004)."""
    connection = connect(tmp_path / "m.db")
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_busy_timeout_is_set(tmp_path: Path) -> None:
    """A reader arriving mid-commit waits rather than raising 'database is locked'."""
    connection = connect(tmp_path / "m.db")
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


# --------------------------------------------------------------------------- #
# The telemetry path: fire and forget
# --------------------------------------------------------------------------- #


async def test_record_does_not_block(ledger: Ledger) -> None:
    """It is not even a coroutine -- the token path must not be able to await on us."""
    assert ledger.record(_batch()) is True


async def test_a_batch_is_written(ledger: Ledger) -> None:
    ledger.record(_batch())
    await ledger.flush()
    row = (
        ledger._require_connection()
        .execute("SELECT * FROM requests WHERE request_id='req_1'")
        .fetchone()
    )
    assert row["tenant_id"] == "demo"
    assert row["route_reason"] == "stakes_low"


async def test_signals_decisions_and_spend_commit_together(ledger: Ledger) -> None:
    """One transaction per request, so the console never renders a half-written one."""
    ledger.record(
        _batch(
            signals=[SignalReading(name="injection", raw=0.1, prob=0.05)],
            decisions=[_decision()],
            spend=[SpendEntry(component="upstream", tokens=120, inr=0.07)],
        )
    )
    await ledger.flush()
    connection = ledger._require_connection()
    assert connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM spend").fetchone()[0] == 1


async def test_spans_commit_with_the_request(ledger: Ledger) -> None:
    ledger.record(
        _batch(
            spans=[
                SpanEntry(
                    trace_id="trc_1",
                    name="interlock.request",
                    duration_ms=12.0,
                    attributes={"interlock.stakes_id": "stk_1"},
                )
            ]
        )
    )
    await ledger.flush()
    row = ledger._require_connection().execute("SELECT * FROM spans").fetchone()
    assert row["trace_id"] == "trc_1"
    assert json.loads(row["attributes_json"])["interlock.stakes_id"] == "stk_1"


async def test_the_full_loss_table_is_stored(ledger: Ledger) -> None:
    """The table IS the explanation. An auditor asking 'why not hold?' needs the row."""
    ledger.record(_batch(decisions=[_decision()]))
    await ledger.flush()
    row = ledger._require_connection().execute("SELECT loss_table_json FROM decisions").fetchone()
    assert json.loads(row[0])[0]["action"] == "L0_pass"


async def test_an_uncalibrated_signal_stores_prob_as_null(ledger: Ledger) -> None:
    """NULL is how the console shows 'we did not calibrate this' (ADR-002)."""
    ledger.record(_batch(signals=[SignalReading(name="pii_leak", raw=1.0, prob=None)]))
    await ledger.flush()
    assert ledger._require_connection().execute("SELECT prob FROM signals").fetchone()[0] is None


async def test_dropped_detectors_are_recorded(ledger: Ledger) -> None:
    """Never silently empty: a degraded request must be identifiable afterwards."""
    ledger.record(_batch(degraded=True, dropped_detectors=["injection"]))
    await ledger.flush()
    row = (
        ledger._require_connection()
        .execute("SELECT degraded, dropped_detectors FROM requests")
        .fetchone()
    )
    assert row[0] == 1
    assert json.loads(row[1]) == ["injection"]


async def test_a_full_queue_drops_and_counts(tmp_path: Path) -> None:
    """A silently lossy ledger makes the cost numbers quietly wrong, which is worse
    than a gap somebody can see."""
    instance = Ledger(db_path=tmp_path / "t.db", max_queue=2)
    await instance.start()
    try:
        accepted = [instance.record(_batch(f"req_{i}")) for i in range(40)]
        assert False in accepted  # some were refused
        assert instance.stats()["dropped"] > 0
    finally:
        await instance.stop()


async def test_stop_drains_what_was_accepted(tmp_path: Path) -> None:
    """We may refuse a write, but we never lose one we accepted."""
    instance = Ledger(db_path=tmp_path / "t.db")
    await instance.start()
    for i in range(25):
        instance.record(_batch(f"req_{i}"))
    await instance.stop()

    connection = connect(tmp_path / "t.db")
    assert connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 25


async def test_a_bad_batch_does_not_kill_the_writer(ledger: Ledger) -> None:
    """Telemetry must never take the process down with it."""
    ledger.record(_batch("req_bad", ts="not-a-number"))  # type: ignore[arg-type]
    ledger.record(_batch("req_good"))
    await ledger.flush()
    assert (
        ledger._require_connection()
        .execute("SELECT COUNT(*) FROM requests WHERE request_id='req_good'")
        .fetchone()[0]
        == 1
    )


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #


async def test_prompts_are_hashed_by_default(ledger: Ledger) -> None:
    """Five lines that answer the enterprise-privacy question."""
    ledger.record(_batch(prompt_text="my PAN is ABCDE1234F"))
    await ledger.flush()
    row = (
        ledger._require_connection()
        .execute("SELECT prompt_hash, prompt_text FROM requests")
        .fetchone()
    )
    assert row["prompt_text"] is None
    assert row["prompt_hash"] and len(row["prompt_hash"]) == 64


async def test_prompts_are_stored_only_when_asked(tmp_path: Path) -> None:
    instance = Ledger(db_path=tmp_path / "t.db", store_prompts=True)
    await instance.start()
    try:
        instance.record(_batch(prompt_text="hello"))
        await instance.flush()
        row = instance._require_connection().execute("SELECT prompt_text FROM requests").fetchone()
        assert row["prompt_text"] == "hello"
    finally:
        await instance.stop()


# --------------------------------------------------------------------------- #
# Holds: the exception that must never be lost
# --------------------------------------------------------------------------- #


async def test_a_hold_is_durable_immediately(ledger: Ledger) -> None:
    await ledger.persist_hold(
        hold_id="hld_1",
        request_id="req_1",
        kind="tool_call",
        reason="irreversible x untrusted_provenance",
        evidence=["d044"],
    )
    assert [h["hold_id"] for h in ledger.pending_holds()] == ["hld_1"]


async def test_a_hold_survives_a_restart(tmp_path: Path) -> None:
    """F6/F7, and the difference between a demo and a product. Kill the process
    mid-hold, start it again, and the review card is still there."""
    db = tmp_path / "t.db"

    first = Ledger(db_path=db)
    await first.start()
    await first.persist_hold(hold_id="hld_1", request_id="req_1", kind="tool_call", reason="frozen")
    # No graceful stop -- simulate a kill.
    first._require_connection().close()

    second = Ledger(db_path=db)
    await second.start()
    try:
        pending = second.pending_holds()
        assert [h["hold_id"] for h in pending] == ["hld_1"]
        assert pending[0]["reason"] == "frozen"
    finally:
        await second.stop()


async def test_a_hold_can_be_approved(ledger: Ledger) -> None:
    await ledger.persist_hold(hold_id="hld_1", request_id="req_1", kind="tool_call")
    assert await ledger.resolve_hold("hld_1", state="approved", resolved_by="reviewer@bank") is True
    assert ledger.pending_holds() == []


async def test_a_hold_cannot_be_resolved_twice(ledger: Ledger) -> None:
    """Two reviewers clicking approve must not execute an irreversible action twice."""
    await ledger.persist_hold(hold_id="hld_1", request_id="req_1", kind="tool_call")
    assert await ledger.resolve_hold("hld_1", state="approved", resolved_by="a") is True
    assert await ledger.resolve_hold("hld_1", state="rejected", resolved_by="b") is False


async def test_resolving_records_who_did_it(ledger: Ledger) -> None:
    """The EU AI Act Art. 14 artefact needs the human, not just the outcome."""
    await ledger.persist_hold(hold_id="hld_1", request_id="req_1", kind="response")
    await ledger.resolve_hold("hld_1", state="approved", resolved_by="reviewer@bank")
    row = (
        ledger._require_connection()
        .execute("SELECT resolved_by, resolved_ts FROM holds WHERE hold_id='hld_1'")
        .fetchone()
    )
    assert row["resolved_by"] == "reviewer@bank"
    assert row["resolved_ts"] is not None


async def test_economics_snapshot_reports_missing_measurements(ledger: Ledger) -> None:
    snapshot = ledger.economics_snapshot()
    assert snapshot["requests"] == 0
    assert snapshot["net_value_inr"] is None
    assert snapshot["net_value_ci_inr"] is None
    assert snapshot["upstream_spend_basis"] == "unmeasured"
    assert any("regret is unmeasured" in note for note in snapshot["notes"])
    assert any("rework edges" in note for note in snapshot["notes"])


async def test_economics_snapshot_exposes_net_value(ledger: Ledger) -> None:
    ledger.record(
        _batch(
            prompt_tokens=100,
            completion_tokens=50,
            spend=[
                SpendEntry(component="upstream", model="qwen3:4b", tokens=150, inr=1.0),
                SpendEntry(component="observer", model="probe", tokens=10, inr=0.1),
            ],
        )
    )
    await ledger.flush()
    baseline = ledger.economics_snapshot()
    assert baseline["regret_inr"] is None
    assert baseline["rework_inr"] is None
    assert baseline["net_value_inr"] is None
    assert baseline["net_value_ci_inr"] is None
    connection = ledger._require_connection()
    connection.execute(
        "INSERT INTO rework_edges(child_request_id, parent_request_id, kind, confidence,"
        " inr_charged, ts) VALUES ('c','p','retry',0.8,2.0,1.0)"
    )
    connection.execute(
        "INSERT INTO shadow_runs(request_id, cheaper_model, verdict, judged_by,"
        " inr_saved_if_switched, ts) VALUES ('r','qwen3:4b','parity','risk',0.4,1.0)"
    )
    snapshot = ledger.economics_snapshot()
    assert snapshot["verification_cost_ratio"] == 0.1
    assert snapshot["regret_inr"] == 0.4
    assert snapshot["rework_inr"] == 2.0
    expected_net = snapshot["routing_savings_inr"] - snapshot["verification_spend_inr"] - 2.4
    assert snapshot["net_value_inr"] == pytest.approx(expected_net)
    assert snapshot["net_value_ci_inr"][0] == snapshot["net_value_ci_inr"][1]
    assert snapshot["net_value_ci_inr"][0] == pytest.approx(snapshot["net_value_inr"])
    assert snapshot["upstream_spend_basis"] == "recorded"
    assert len(snapshot["net_value_ci_inr"]) == 2
    assert snapshot["net_value_samples"] == 1


async def test_lane_c_snapshot_computes_e_value_series(ledger: Ledger) -> None:
    connection = ledger._require_connection()
    for index in range(12):
        connection.execute(
            "INSERT INTO fairness_pairs(pair_id, base_request_id, twin_request_id, attribute,"
            " decision_field, base_value, twin_value, delta, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"pair_{index}",
                f"base_{index}",
                f"twin_{index}",
                "gender",
                "action",
                "L0_pass",
                "L4_hold" if index % 3 == 0 else "L0_pass",
                1.0 if index % 3 == 0 else 0.0,
                float(index),
            ),
        )
    snapshot = ledger.lane_c_snapshot()
    assert snapshot["n_pairs"] == 12
    assert snapshot["by_axis"]["gender"]["disparate"] == 4
    assert len(snapshot["series"]["e_value"]) == 12


async def test_lane_c_pair_writer_persists_observation(ledger: Ledger) -> None:
    await ledger.persist_fairness_pair(
        pair_id="pair-imported",
        base_request_id="base-request",
        twin_request_id="twin-request",
        attribute="gender",
        decision_field="action",
        base_value="L0_pass",
        twin_value="L4_hold",
        delta=1.0,
    )
    row = (
        ledger._require_connection()
        .execute("SELECT attribute, base_value, twin_value, delta FROM fairness_pairs")
        .fetchone()
    )
    assert tuple(row) == ("gender", "L0_pass", "L4_hold", 1.0)


async def test_lane_c_pair_writer_rejects_self_pair(ledger: Ledger) -> None:
    with pytest.raises(ValueError, match="distinct"):
        await ledger.persist_fairness_pair(
            pair_id="bad",
            base_request_id="same",
            twin_request_id="same",
            attribute="gender",
            decision_field="action",
            base_value="L0_pass",
            twin_value="L0_pass",
            delta=0.0,
        )


# --------------------------------------------------------------------------- #
# Contract 5: nothing on the token path writes directly
# --------------------------------------------------------------------------- #


def test_the_token_path_never_imports_sqlite3() -> None:
    """Enforced by inspection rather than by convention, because the whole point of
    the single-writer design is that it cannot be bypassed under deadline pressure."""
    root = Path(__file__).resolve().parents[2] / "interlock"
    for module in ("gateway", "gate", "signals", "risk"):
        for path in (root / module).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in source, f"{path} writes to SQLite directly"


@pytest.mark.chaos
async def test_concurrent_writers_do_not_lock(tmp_path: Path) -> None:
    """The failure the plan names as showing up under load."""
    instance = Ledger(db_path=tmp_path / "t.db")
    await instance.start()
    try:
        await asyncio.gather(
            *(asyncio.to_thread(instance.record, _batch(f"req_{i}")) for i in range(200))
        )
        await instance.flush()
        connection = instance._require_connection()
        assert connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0] > 0
    finally:
        await instance.stop()


def test_duckdb_can_attach_read_only(tmp_path: Path) -> None:
    """The console's analytics path (ADR-004): reads must never block a writer."""
    duckdb = pytest.importorskip("duckdb")
    db = tmp_path / "t.db"
    connection = connect(db)
    apply_migrations(connection)
    connection.execute(
        "INSERT INTO requests(request_id, trace_id, tenant_id, ts) VALUES ('r','t','demo',1.0)"
    )
    connection.close()

    analytics = duckdb.connect()
    analytics.execute("INSTALL sqlite; LOAD sqlite;")
    analytics.execute(f"ATTACH '{db.as_posix()}' AS ledger (TYPE sqlite, READ_ONLY)")
    assert analytics.execute("SELECT COUNT(*) FROM ledger.requests").fetchone()[0] == 1


def test_sqlite_row_factory_gives_named_access(tmp_path: Path) -> None:
    connection = connect(tmp_path / "t.db")
    apply_migrations(connection)
    assert isinstance(connection.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)
