"""The evidence pack: everything needed to defend one decision, in a zip.

EU AI Act Article 12 requires automatic logging over a system's lifetime; Article 14
requires that a human overseeing it can understand and, where needed, reverse its
output. Both come down to the same practical question, asked months later by somebody
who was not there:

    *Why did the system do that, and on what basis?*

Answering it needs more than a decision record. It needs the **inputs** the decision was
made from, the **loss table** showing what the alternatives would have cost, the exact
**policy and calibration versions** that priced it, and — where a human was involved —
**who** decided and when. Any one of those missing turns the pack from evidence into an
assertion.

The build is deliberately dull: a ``zipfile.write`` loop over rows already in the ledger.
Nothing is computed here that was not recorded at the time, because a number regenerated
at export time is not evidence of what happened, it is evidence of what today's code
thinks happened.

**Three things this file is careful about.**

*Prompts may be hashed.* ``INTERLOCK_STORE_PROMPTS`` defaults to off, so the ledger holds
a digest rather than the customer's text. The pack says so explicitly rather than
shipping a field that silently reads ``null`` — an auditor should learn that the content
was deliberately not retained, not wonder whether it was lost.

*Canary strings never appear.* They are per-tenant secrets (CLAUDE.md §9), and an
evidence pack is precisely the artefact most likely to be emailed around. Redacted on the
way out, and asserted by a test.

*A missing field is reported, not filled in.* ``completeness`` lists what could not be
found. An export that quietly omits the loss table looks identical to one where the loss
table was empty.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["EvidencePack", "build_evidence_pack"]

#: Fields whose absence makes the pack materially weaker. Reported, never fabricated.
REQUIRED_FIELDS: tuple[str, ...] = (
    "decision",
    "loss_table",
    "policy_version",
    "calib_version",
    "inputs_digest",
)

#: Keys whose values are redacted wherever they appear, at any depth.
_SECRET_KEYS = frozenset({"canary", "canaries", "resume_token", "api_key", "authorization"})


def _redact(value: Any, *, canaries: Sequence[str] = ()) -> Any:
    """Strip secrets from an arbitrary JSON-ish structure.

    Recursive and key-based rather than a regex over the serialised blob: a regex would
    have to know every shape a secret can take, and would miss the one somebody adds
    next month.
    """
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if key.lower() in _SECRET_KEYS else _redact(item, canaries=canaries))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, canaries=canaries) for item in value]
    if isinstance(value, str):
        redacted = value
        for canary in canaries:
            if canary and canary in redacted:
                redacted = redacted.replace(canary, "[REDACTED-CANARY]")
        return redacted
    return value


@dataclass
class EvidencePack:
    """One request's full record, assembled and ready to write."""

    request_id: str
    generated_ts: float
    request: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    spend: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    holds: list[dict[str, Any]] = field(default_factory=list)
    #: The policy file as it stood, verbatim. Not a version string -- the file.
    policy_text: str = ""
    calibration: dict[str, Any] = field(default_factory=dict)
    #: What could not be found, and therefore is not in here.
    completeness: list[str] = field(default_factory=list)
    #: True when prompts were stored as digests rather than text.
    prompts_hashed: bool = True

    def manifest(self) -> dict[str, Any]:
        """The index. Read first by anyone opening the pack."""
        return {
            "request_id": self.request_id,
            "generated_ts": self.generated_ts,
            "contents": {
                "request.json": "the proxied request: stakes, routing, timings",
                "decisions.json": "one record per sentence, each with its full loss table",
                "signals.json": "the calibrated detector readings behind the probabilities",
                "spend.json": "token spend by component",
                "tool_calls.json": "tool calls, their provenance and whether they ran",
                "holds.json": "durable holds, and who resolved them",
                "policy.yaml": "the policy file AS IT STOOD, verbatim",
                "calibration.json": "the calibration artefacts that produced the probabilities",
            },
            "completeness": self.completeness,
            "complete": not self.completeness,
            "prompts_hashed": self.prompts_hashed,
            "notes": [
                (
                    "Prompts are stored as digests, not text (INTERLOCK_STORE_PROMPTS=0). "
                    "The content was deliberately not retained; it was not lost."
                    if self.prompts_hashed
                    else "Prompts are stored verbatim (INTERLOCK_STORE_PROMPTS=1)."
                ),
                "Tenant canary strings are redacted throughout (CLAUDE.md s9).",
                (
                    "Nothing here is recomputed at export time. Every value is what was "
                    "recorded when the decision was made."
                ),
            ],
        }

    def write(self, path: Path | str, *, canaries: Sequence[str] = ()) -> Path:
        """Write the zip. Returns the path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes(canaries=canaries))
        return path

    def to_bytes(self, *, canaries: Sequence[str] = ()) -> bytes:
        """Return the same evidence ZIP as ``write`` without a temporary file."""
        payloads: dict[str, Any] = {
            "manifest.json": self.manifest(),
            "request.json": self.request,
            "decisions.json": self.decisions,
            "signals.json": self.signals,
            "spend.json": self.spend,
            "tool_calls.json": self.tool_calls,
            "holds.json": self.holds,
            "calibration.json": self.calibration,
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in payloads.items():
                archive.writestr(
                    name, json.dumps(_redact(payload, canaries=canaries), indent=2, default=str)
                )
            if self.policy_text:
                archive.writestr("policy.yaml", _redact(self.policy_text, canaries=canaries))
        return output.getvalue()


def build_evidence_pack(
    *,
    request_id: str,
    rows: dict[str, Any],
    policy_text: str = "",
    calibration: dict[str, Any] | None = None,
    generated_ts: float = 0.0,
    prompts_hashed: bool = True,
) -> EvidencePack:
    """Assemble a pack from ledger rows, recording whatever is missing.

    ``rows`` is whatever the ledger returned; nothing is required to be present. What is
    absent is listed in ``completeness`` rather than defaulted, because an export that
    quietly omits the loss table looks identical to one where the loss table was empty.
    """
    decisions = list(rows.get("decisions") or [])
    completeness: list[str] = []

    if not rows.get("request"):
        completeness.append("request: no row found for this request_id")
    if not decisions:
        completeness.append("decisions: none recorded -- the request may not have been buffered")
    else:
        without_table = sum(1 for d in decisions if not d.get("loss_table"))
        if without_table:
            completeness.append(
                f"loss_table: missing on {without_table} of {len(decisions)} decisions; "
                f"the table IS the explanation, so those decisions are unexplained"
            )
    if not policy_text:
        completeness.append("policy.yaml: not captured -- the pricing cannot be re-derived")
    if not calibration:
        completeness.append(
            "calibration.json: not captured -- the probabilities cannot be traced to a fit"
        )

    for row in decisions:
        for key in ("policy_version", "calib_version"):
            if not row.get(key):
                completeness.append(
                    f"{key}: absent on decision {row.get('decision_id', '?')}; "
                    f"which version priced it cannot be answered"
                )
                break

    return EvidencePack(
        request_id=request_id,
        generated_ts=generated_ts,
        request=dict(rows.get("request") or {}),
        decisions=decisions,
        signals=list(rows.get("signals") or []),
        spend=list(rows.get("spend") or []),
        tool_calls=list(rows.get("tool_calls") or []),
        holds=list(rows.get("holds") or []),
        policy_text=policy_text,
        calibration=dict(calibration or {}),
        completeness=completeness,
        prompts_hashed=prompts_hashed,
    )
