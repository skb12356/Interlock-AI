"""Contract 4 — policy as code (Implementation03 §5).

One YAML file per tenant per industry, Pydantic-validated at boot, with
``policy_version`` derived from a sha256 of the file contents so a decision can always
be traced back to the exact bytes that priced it.

**Why this file is a feature, not configuration.** Every competing product encodes the
same judgement inside an opaque ``0.7`` picked by an engineer. This one is diffable,
reviewable by risk and compliance, and stamped on every decision. "Who decided a wrong
answer is worth Rs.40,000?" has an answer, and the answer is a file with a history.

Failure behaviour, which differs deliberately between the two paths:

* **At boot** an invalid policy raises and the process refuses to start. Running on a
  policy nobody validated is worse than not running.
* **On hot reload** an invalid policy is rejected and the previous version stays live. A
  running system on a known-good policy beats a stopped system on a correct one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from interlock.core.errors import PolicyError
from interlock.core.types import ACTIONS, Action, Defect, Reversibility

__all__ = [
    "DomainStakes",
    "EfficacyEntry",
    "Guarantees",
    "HumanReview",
    "Policy",
    "StakesPolicy",
    "Thresholds",
    "ToolPolicy",
    "load_policy",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainStakes(_Strict):
    impact_inr: float = Field(ge=0)
    reversibility: Reversibility


class Multipliers(_Strict):
    reversibility: dict[Reversibility, float]
    defect: dict[Defect, float]
    monetary_amount_over_inr: dict[float, float] = Field(default_factory=dict)

    @field_validator("reversibility")
    @classmethod
    def _all_reversibility_classes(
        cls, value: dict[Reversibility, float]
    ) -> dict[Reversibility, float]:
        missing = {"reversible", "costly", "irreversible"} - set(value)
        if missing:
            raise ValueError(f"missing reversibility multipliers: {sorted(missing)}")
        return value


class StakesPolicy(_Strict):
    default_impact_inr: float = Field(ge=0)
    domains: dict[str, DomainStakes]
    multipliers: Multipliers


class EfficacyEntry(_Strict):
    """How much of a defect an action actually removes, and where that number came from.

    ADR-009: a prior on Day 1, measured with a Wilson interval by Day 5. The ``source``
    field is what lets the console — and the pitch — distinguish the two honestly
    instead of presenting a guess as a measurement.
    """

    value: float = Field(ge=0.0, le=1.0)
    source: str = "prior"  # 'prior' | 'measured' | 'definitional'
    lo: float | None = None  # Wilson lower bound, when measured
    hi: float | None = None  # Wilson upper bound, when measured
    n: int | None = None  # sample size behind the measurement

    @property
    def measured(self) -> bool:
        return self.source == "measured"


class ToolPolicy(_Strict):
    reversibility: Reversibility
    max_auto_inr: float | None = None


class HumanReview(_Strict):
    cost_inr: float = Field(ge=0)
    sla_minutes: int = Field(gt=0)


class Guarantees(_Strict):
    max_ungrounded_escape_rate: float = Field(gt=0, lt=1)
    confidence: float = Field(gt=0, lt=1)

    @property
    def alpha(self) -> float:
        """The risk level the conformal threshold search must certify."""
        return self.max_ungrounded_escape_rate

    @property
    def delta(self) -> float:
        """The failure probability of the bound: 1 - confidence."""
        return 1.0 - self.confidence


class Thresholds(_Strict):
    """The single number that spends both budgets.

    Router and guardrail read the *same* stakes estimate against these thresholds. They
    are separate fields so the two can be tuned independently if a deployment demands
    it -- but they are read from one estimate, which is the claim.
    """

    buffer_above_impact_inr: float = 1000.0
    strong_model_above_impact_inr: float = 1000.0


class Policy(_Strict):
    version: str
    currency: str = "INR"
    lambda_time_inr_per_second: float = Field(ge=0)
    minimum_relative_action_gain: float = Field(default=0.0, ge=0.0, lt=1.0)
    stakes: StakesPolicy
    nuisance_inr: dict[Action, float]
    latency_ms: dict[Action, float]
    compute_tokens: dict[Action, float]
    #: Blended token price for term (3). D4-A1 replaces this with per-model pricing.
    price_inr_per_1k_tokens: float = Field(default=0.0, ge=0)
    efficacy: dict[Action, dict[Defect, EfficacyEntry]] = Field(default_factory=dict)
    tools: dict[str, ToolPolicy]
    human_review: HumanReview
    guarantees: Guarantees
    thresholds: Thresholds = Field(default_factory=Thresholds)

    #: sha256 of the file this was loaded from. Stamped on every decision.
    policy_version: str = ""
    source_path: str | None = None

    # -- validation ------------------------------------------------------- #

    @field_validator("nuisance_inr", "latency_ms", "compute_tokens")
    @classmethod
    def _every_action_is_priced(cls, value: dict[Action, float]) -> dict[Action, float]:
        """A missing row would silently make an action look free.

        The loss table prices every rung of the ladder, so every rung needs a price.
        """
        missing = set(ACTIONS) - set(value)
        if missing:
            raise ValueError(f"every action must be priced; missing: {sorted(missing)}")
        return value

    @field_validator("tools")
    @classmethod
    def _default_tool_policy_exists(cls, value: dict[str, ToolPolicy]) -> dict[str, ToolPolicy]:
        """An unknown tool must still have a reversibility class, or the interlock has
        nothing to look up at exactly the moment it matters most."""
        if "default" not in value:
            raise ValueError("tools must define a 'default' entry for unknown tools")
        return value

    # -- lookups ---------------------------------------------------------- #

    def domain(self, name: str) -> DomainStakes:
        """Stakes for a domain, falling back to the default impact when unknown."""
        found = self.stakes.domains.get(name)
        if found is not None:
            return found
        return DomainStakes(impact_inr=self.stakes.default_impact_inr, reversibility="reversible")

    def monetary_multiplier(self, amount_inr: float) -> float:
        """The multiplier for the largest monetary amount in play.

        Bands are applied as "at least this much", taking the highest band that the
        amount clears, so adding a new band never silently lowers an existing one.
        """
        multiplier = 1.0
        for threshold, value in sorted(self.stakes.multipliers.monetary_amount_over_inr.items()):
            if amount_inr >= threshold:
                multiplier = value
        return multiplier

    def impact_for(
        self,
        *,
        impact_inr: float,
        defect: Defect,
        reversibility: Reversibility,
        monetary_amount_inr: float = 0.0,
    ) -> float:
        """``Impact_d`` — derived from the policy, never typed into the code.

        ``stakes.impact_inr x class_multiplier[d] x reversibility_multiplier[r]``, plus
        the monetary band. A reviewer diffs a YAML file, not a threshold buried in code.
        """
        return (
            impact_inr
            * self.stakes.multipliers.defect[defect]
            * self.stakes.multipliers.reversibility[reversibility]
            * self.monetary_multiplier(monetary_amount_inr)
        )

    def efficacy_for(self, action: Action, defect: Defect) -> float:
        """How much of ``defect`` this action removes. Unknown pairs remove nothing.

        Defaulting to 0.0 is the conservative direction: an action gets no credit for
        removing a defect nobody has measured it against.
        """
        entry = self.efficacy.get(action, {}).get(defect)
        return entry.value if entry is not None else 0.0

    def tool(self, name: str) -> ToolPolicy:
        return self.tools.get(name, self.tools["default"])

    @property
    def uses_only_measured_efficacy(self) -> bool:
        """True once every efficacy entry came from a measurement (ADR-009, Day 5)."""
        return all(
            entry.source in {"measured", "definitional"}
            for per_action in self.efficacy.values()
            for entry in per_action.values()
        )


def _coerce_efficacy(raw: Any) -> Any:
    """Accept a bare float as shorthand for ``{value: x, source: prior}``.

    The plan's example policy writes bare floats; the measured form needs an interval.
    Supporting both keeps the documented file valid while the Day-5 write-back adds
    provenance to each number.
    """
    if not isinstance(raw, dict):
        return raw
    out: dict[str, Any] = {}
    for action, per_defect in raw.items():
        if not isinstance(per_defect, dict):
            out[action] = per_defect
            continue
        out[action] = {
            defect: ({"value": entry} if isinstance(entry, int | float) else entry)
            for defect, entry in per_defect.items()
        }
    return out


def load_policy(path: str | Path) -> Policy:
    """Load and validate a policy file, stamping it with a stable content digest.

    Raises ``PolicyError`` on anything invalid — the caller decides whether that means
    "refuse to start" (boot) or "keep the previous version live" (hot reload).
    """
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc

    # Git checkouts may use CRLF on Windows and LF on Linux. Hash the canonical YAML
    # bytes so a policy keeps one identity across deployment platforms.
    canonical_bytes = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    try:
        data = yaml.safe_load(canonical_bytes.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file {path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise PolicyError(f"policy file {path} must contain a mapping at the top level")

    data = dict(data)
    if "efficacy" in data:
        data["efficacy"] = _coerce_efficacy(data["efficacy"])

    digest = hashlib.sha256(canonical_bytes).hexdigest()
    data["policy_version"] = f"{data.get('version', 'unknown')}@sha256:{digest[:16]}"
    data["source_path"] = str(path)

    try:
        return Policy.model_validate(data)
    except ValidationError as exc:
        raise PolicyError(f"policy file {path} failed validation:\n{exc}") from exc
