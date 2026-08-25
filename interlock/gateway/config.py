"""Gateway configuration.

Everything comes from the environment or ``policies/`` — never from a hard-coded
constant and never from an account a judge does not have (CLAUDE.md §9,
Implementation02 §1.3). The defaults are chosen so that a clean checkout with **no API
key at all** runs the full system against a local Ollama.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["ModelTier", "Settings", "load_settings"]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ModelTier:
    """One rung of the routing ladder.

    Two local Ollama models give a genuine two-tier router, so routing savings and the
    cost-regret ledger are measured against real spend rather than a simulated price
    table (deviation D-003).
    """

    name: str
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class Settings:
    # -- providers ---------------------------------------------------------- #
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # -- routing ------------------------------------------------------------ #
    #: The cheap tier. ~80% of traffic should land here; that subsidy is what pays
    #: for the checking on the rest.
    cheap_tier: ModelTier = field(default_factory=lambda: ModelTier("cheap", "ollama", "qwen3:4b"))
    #: The strong tier, for traffic the stakes estimate says is worth it.
    strong_tier: ModelTier = field(
        default_factory=lambda: ModelTier("strong", "ollama", "qwen3:8b")
    )

    # -- observer ----------------------------------------------------------- #
    observer_base_url: str = "http://127.0.0.1:8081"

    # -- budgets ------------------------------------------------------------ #
    #: Lane A's hard deadline. A detector that misses it is dropped, not awaited.
    #:
    #: The plan specifies a ~25 ms budget with a 40 ms hard deadline. Raised to 120 ms
    #: for this build (deviation D-008) because the CPU-only profile has no ONNX-
    #: accelerated detectors. **The consequence is a claim, not just a number:** the
    #: low-stakes tail may now reach 120 ms, which breaks the "<= 40 ms added p95 on
    #: low-stakes traffic" target and spends the whole headline budget here. Lower it
    #: with INTERLOCK_LANE_A_DEADLINE_MS once the detectors are ONNX-exported, and
    #: report the *measured* p95 at D5-A2 either way.
    lane_a_deadline_ms: float = 120.0
    #: What the observer is given per sentence; its own timeout adds a 30 ms margin.
    observe_deadline_ms: float = 120.0
    #: The gate's per-sentence watchdog: if the model stalls mid-sentence, flush.
    sentence_watchdog_s: float = 8.0
    #: How long Ollama keeps a model resident after a request. See F-014: the default
    #: 5 minutes means a demo that pauses between scenes pays a 12-21 s cold start on
    #: the next one, which swamps every latency number Interlock is trying to measure.
    ollama_keep_alive: str = "30m"
    upstream_connect_timeout_s: float = 10.0
    upstream_read_timeout_s: float = 120.0

    # -- storage ------------------------------------------------------------ #
    db_path: Path = REPO_ROOT / "data" / "interlock.db"
    policy_path: Path = REPO_ROOT / "policies" / "banking.yaml"
    #: Built offline by ``scripts/build_index.py`` and opened read-only. Separate from
    #: db_path on purpose: the ledger is written by exactly one task (Contract 5), the
    #: index is never written during a request at all.
    corpus_index_path: Path = REPO_ROOT / "data" / "corpus.db"
    #: 'real' | 'stub'. The Day-3 exit criterion is "no stub on the hot path", so the
    #: default is real. 'stub' keeps the X-Interlock-Force header working, which is how
    #: the chaos tests and the demo script drive specific failures on demand -- a
    #: deliberate affordance from the plan, and one that must never be the default in a
    #: deployment that serves anyone.
    risk_engine: str = "real"
    #: Where scripts/calibrate.py wrote its artefacts. A missing calibrator does not
    #: stop the gateway; it makes every decision degraded and says so.
    calibration_dir: Path = REPO_ROOT / "artifacts" / "calibration"
    #: Guaranteed mode. OFF by default -- see finding F-016: the certified threshold
    #: currently strikes L0_pass on 100% of traffic, so turning this on trades the
    #: false-intervention target for the escape guarantee. That is a deployment choice,
    #: not a default.
    conformal_filter: bool = False
    #: 'hashing-v1' (deterministic, no torch) or a sentence-transformers model name.
    #: Must match what built the index, or the index refuses to open -- deliberately.
    embedder: str = "hashing-v1"
    #: How many passages reach the context window. Enough for a repair to have
    #: something to correct against, few enough not to bury the flagged claim.
    retrieval_k: int = 4

    # -- privacy ------------------------------------------------------------ #
    #: Prompts are stored **hashed** unless this is set. Five lines of behaviour that
    #: buy a whole answer to the enterprise-privacy question (Implementation02 §3).
    store_prompts: bool = False

    tenant_id: str = "demo"

    @property
    def tiers(self) -> dict[str, ModelTier]:
        return {"cheap": self.cheap_tier, "strong": self.strong_tier}


def load_settings() -> Settings:
    """Read settings from the environment, falling back to the keyless local defaults."""
    return Settings(
        ollama_base_url=_env("INTERLOCK_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        anthropic_base_url=_env("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        cheap_tier=ModelTier(
            "cheap",
            _env("INTERLOCK_CHEAP_PROVIDER", "ollama"),
            _env("INTERLOCK_CHEAP_MODEL", "qwen3:4b"),
        ),
        strong_tier=ModelTier(
            "strong",
            _env("INTERLOCK_STRONG_PROVIDER", "ollama"),
            _env("INTERLOCK_STRONG_MODEL", "qwen3:8b"),
        ),
        observer_base_url=_env("INTERLOCK_OBSERVER_URL", "http://127.0.0.1:8081"),
        ollama_keep_alive=_env("INTERLOCK_OLLAMA_KEEP_ALIVE", "30m"),
        lane_a_deadline_ms=_env_float("INTERLOCK_LANE_A_DEADLINE_MS", 120.0),
        observe_deadline_ms=_env_float("INTERLOCK_OBSERVE_DEADLINE_MS", 120.0),
        sentence_watchdog_s=_env_float("INTERLOCK_SENTENCE_WATCHDOG_S", 8.0),
        db_path=Path(_env("INTERLOCK_DB_PATH", str(REPO_ROOT / "data" / "interlock.db"))),
        policy_path=Path(
            _env("INTERLOCK_POLICY_PATH", str(REPO_ROOT / "policies" / "banking.yaml"))
        ),
        corpus_index_path=Path(
            _env("INTERLOCK_CORPUS_INDEX_PATH", str(REPO_ROOT / "data" / "corpus.db"))
        ),
        risk_engine=_env("INTERLOCK_RISK_ENGINE", "real"),
        calibration_dir=Path(
            _env("INTERLOCK_CALIBRATION_DIR", str(REPO_ROOT / "artifacts" / "calibration"))
        ),
        conformal_filter=_env_bool("INTERLOCK_CONFORMAL_FILTER", False),
        embedder=_env("INTERLOCK_EMBEDDER", "hashing-v1"),
        retrieval_k=int(_env_float("INTERLOCK_RETRIEVAL_K", 4.0)),
        store_prompts=_env_bool("INTERLOCK_STORE_PROMPTS", False),
        tenant_id=_env("INTERLOCK_TENANT_ID", "demo"),
    )
