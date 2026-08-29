"""Run the repository's focused security/privacy checks and write evidence.

This is a bounded application sweep, not a substitute for an external penetration test.
It checks the controls that can be proven locally without provider credentials.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.policy import load_policy  # noqa: E402
from interlock.gateway.config import Settings  # noqa: E402
from interlock.ledger.evidence import build_evidence_pack  # noqa: E402
from interlock.signals.canary import CanaryRegistry  # noqa: E402


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    checks["prompt_storage_defaults_off"] = Settings().store_prompts is False
    details["prompt_storage_defaults_off"] = "INTERLOCK_STORE_PROMPTS defaults to false"

    registry = CanaryRegistry()
    first = registry.mint("tenant-a")
    second = registry.mint("tenant-b")
    checks["tenant_canary_isolation"] = (
        registry.owner_of(first) == "tenant-a"
        and registry.owner_of(second) == "tenant-b"
        and first not in registry.canaries_for("tenant-b")
    )
    details["tenant_canary_isolation"] = "canaries are owner-bound and not shared between tenants"

    secret = "INTERLOCK-CANARY-SECURITY-SWEEP"
    pack = build_evidence_pack(
        request_id="security-sweep",
        rows={"holds": [{"resume_token": "resume-secret", "reason": secret}]},
        policy_text="canary: " + secret,
    )
    with zipfile.ZipFile(BytesIO(pack.to_bytes(canaries=[secret]))) as archive:
        rendered = "\n".join(
            archive.read(name).decode("utf-8") for name in archive.namelist()
        )
    checks["evidence_redaction"] = "resume-secret" not in rendered and secret not in rendered
    details["evidence_redaction"] = "resume tokens and tenant canaries are absent from ZIP bytes"

    tracked = subprocess.run(
        ["git", "ls-files", ".env", ".env.local", "*.key", "canaries.local.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    checks["secret_files_untracked"] = not tracked.stdout.strip()
    details["secret_files_untracked"] = "git does not track configured secret-file patterns"

    checks["policy_loads"] = bool(load_policy(REPO_ROOT / "policies" / "banking.yaml").policy_version)
    details["policy_loads"] = "versioned policy validates through the strict schema"

    result = {"checks": checks, "details": details, "passed": all(checks.values())}
    output = REPO_ROOT / "artifacts" / "security" / "security_sweep.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
