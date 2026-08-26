"""Evidence pack export.

The pack is the artefact somebody opens months later to ask why the system did what it
did. Two failures would make it worse than useless, and both are tested here:

* **A canary string in the zip.** These are per-tenant secrets, and an evidence pack is
  precisely the artefact most likely to be emailed around.
* **A silently incomplete pack.** An export missing the loss table looks identical to one
  where the loss table was empty. If the pack cannot answer "which version priced this?",
  it has to say so on the front page rather than leave a reader to notice.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from interlock.ledger.evidence import EvidencePack, build_evidence_pack

CANARY = "INTERLOCK-CANARY-acme-0f3a91b2c4d5e6f7"


def _rows() -> dict:
    return {
        "request": {
            "request_id": "req_1",
            "stakes_inr": 40000.0,
            "route_reason": "stakes_high",
            "model_served": "qwen3:8b",
            "prompt_digest": "sha256:abc123",
        },
        "decisions": [
            {
                "decision_id": "dec_1",
                "sentence_idx": 0,
                "action": "L2_repair",
                "chosen_loss": 494.36,
                "policy_version": "banking-v3@sha256:0e43e9ba",
                "calib_version": "calib@sha256:dd2246eb",
                "inputs_digest": "sha256:def456",
                "loss_table": [
                    {"action": "L0_pass", "total": 2605.18, "available": True},
                    {"action": "L2_repair", "total": 494.36, "available": True},
                ],
            }
        ],
        "signals": [{"name": "grounding.citation_unsupported", "prob": 0.94}],
        "spend": [{"component": "upstream", "tokens": 900, "inr": 0.54}],
        "tool_calls": [],
        "holds": [],
    }


def _pack(**overrides: object) -> EvidencePack:
    payload: dict = {
        "request_id": "req_1",
        "rows": _rows(),
        "policy_text": "version: banking-v3\nlambda_time_inr_per_second: 0.40\n",
        "calibration": {"ece": 0.0037, "n": 10000},
        "generated_ts": 1_800_000_000.0,
    }
    payload.update(overrides)
    return build_evidence_pack(**payload)  # type: ignore[arg-type]


def _read(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode("utf-8")


# --------------------------------------------------------------------------- #
# It answers the question it exists to answer
# --------------------------------------------------------------------------- #


def test_the_pack_contains_everything_needed_to_defend_a_decision(tmp_path: Path) -> None:
    path = _pack().write(tmp_path / "evidence.zip")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    assert {
        "manifest.json",
        "request.json",
        "decisions.json",
        "signals.json",
        "spend.json",
        "tool_calls.json",
        "holds.json",
        "policy.yaml",
        "calibration.json",
    } <= names


def test_the_loss_table_survives_export(tmp_path: Path) -> None:
    """The table IS the explanation. A pack with only the chosen action records what
    happened and not why -- which is the half an auditor actually needs."""
    path = _pack().write(tmp_path / "evidence.zip")
    decisions = json.loads(_read(path, "decisions.json"))
    assert decisions[0]["loss_table"]
    assert any(row["action"] == "L0_pass" for row in decisions[0]["loss_table"])


def test_which_version_priced_it_is_answerable(tmp_path: Path) -> None:
    path = _pack().write(tmp_path / "evidence.zip")
    decisions = json.loads(_read(path, "decisions.json"))
    assert decisions[0]["policy_version"].startswith("banking-v3@")
    assert decisions[0]["calib_version"].startswith("calib@")
    # And the policy file itself, verbatim -- a version string alone cannot be
    # re-derived from once the file has moved on.
    assert "lambda_time_inr_per_second" in _read(path, "policy.yaml")


def test_a_complete_pack_says_it_is_complete(tmp_path: Path) -> None:
    path = _pack().write(tmp_path / "evidence.zip")
    manifest = json.loads(_read(path, "manifest.json"))
    assert manifest["complete"] is True
    assert manifest["completeness"] == []


# --------------------------------------------------------------------------- #
# Secrets never leave
# --------------------------------------------------------------------------- #


def test_a_canary_never_appears_anywhere_in_the_zip(tmp_path: Path) -> None:
    """Per-tenant secrets (CLAUDE.md s9), and this is the artefact most likely to be
    emailed around."""
    rows = _rows()
    rows["decisions"][0]["why"] = [f"canary token matched on egress: {CANARY}"]
    rows["holds"] = [{"hold_id": "h1", "resume_token": "tok_secret", "reason": CANARY}]

    path = _pack(rows=rows).write(tmp_path / "evidence.zip", canaries=[CANARY])
    with zipfile.ZipFile(path) as archive:
        blob = "".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert CANARY not in blob
    assert "[REDACTED-CANARY]" in blob


def test_a_resume_token_never_appears(tmp_path: Path) -> None:
    """It releases an irreversible action. An evidence pack must not be a way to get one."""
    rows = _rows()
    rows["holds"] = [{"hold_id": "h1", "resume_token": "tok_secret_value"}]
    path = _pack(rows=rows).write(tmp_path / "evidence.zip")
    assert "tok_secret_value" not in _read(path, "holds.json")
    assert "[REDACTED]" in _read(path, "holds.json")


def test_redaction_reaches_nested_structures(tmp_path: Path) -> None:
    """A regex over the serialised blob would have to know every shape a secret takes,
    and would miss the one somebody adds next month."""
    rows = _rows()
    rows["tool_calls"] = [{"args": {"headers": {"authorization": "Bearer sk-live-123"}}}]
    path = _pack(rows=rows).write(tmp_path / "evidence.zip")
    assert "sk-live-123" not in _read(path, "tool_calls.json")


def test_hashed_prompts_are_declared_not_left_looking_lost(tmp_path: Path) -> None:
    """An auditor should learn the content was deliberately not retained, rather than
    wonder whether it went missing."""
    path = _pack().write(tmp_path / "evidence.zip")
    manifest = json.loads(_read(path, "manifest.json"))
    assert manifest["prompts_hashed"] is True
    assert any("deliberately not retained" in note for note in manifest["notes"])


# --------------------------------------------------------------------------- #
# Incompleteness is reported, never papered over
# --------------------------------------------------------------------------- #


def test_a_missing_loss_table_is_named_on_the_front_page(tmp_path: Path) -> None:
    rows = _rows()
    rows["decisions"][0]["loss_table"] = []
    path = _pack(rows=rows).write(tmp_path / "evidence.zip")
    manifest = json.loads(_read(path, "manifest.json"))
    assert manifest["complete"] is False
    assert any("loss_table" in item for item in manifest["completeness"])
    assert any("unexplained" in item for item in manifest["completeness"])


def test_a_missing_policy_is_reported() -> None:
    pack = _pack(policy_text="")
    assert any("policy.yaml" in item for item in pack.completeness)
    assert any("cannot be re-derived" in item for item in pack.completeness)


def test_a_missing_calibration_is_reported() -> None:
    pack = _pack(calibration={})
    assert any("calibration.json" in item for item in pack.completeness)


def test_a_decision_with_no_version_stamp_is_reported() -> None:
    """ "Which version priced this?" is the question the pack exists to answer."""
    rows = _rows()
    rows["decisions"][0]["policy_version"] = ""
    pack = _pack(rows=rows)
    assert any("policy_version" in item for item in pack.completeness)


def test_an_empty_ledger_produces_a_pack_that_admits_it(tmp_path: Path) -> None:
    """Exporting nothing must not look like exporting a clean record."""
    pack = build_evidence_pack(request_id="req_missing", rows={})
    path = pack.write(tmp_path / "evidence.zip")
    manifest = json.loads(_read(path, "manifest.json"))
    assert manifest["complete"] is False
    assert len(manifest["completeness"]) >= 4


def test_nothing_is_recomputed_at_export_time(tmp_path: Path) -> None:
    """A number regenerated at export is evidence of what today's code thinks happened,
    not of what happened. The chosen_loss must survive byte-for-byte."""
    rows = _rows()
    rows["decisions"][0]["chosen_loss"] = 12345.6789
    path = _pack(rows=rows).write(tmp_path / "evidence.zip")
    assert json.loads(_read(path, "decisions.json"))[0]["chosen_loss"] == 12345.6789
