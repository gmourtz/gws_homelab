"""IPS (Investment Policy Statement) loader and typed schema.

All agent behaviour is driven by this config.  The LLM does not decide
what actions to take — the IPS does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DEFAULT_IPS_PATH = Path(__file__).parent / "ips.yml"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bucket:
    """One allocation bucket (e.g. 'US Equity', 'Cash')."""

    name: str
    target_pct: float
    band_abs: float = 5.0       # absolute pp band  (5/25 rule)
    band_rel: float = 25.0      # relative % band   (5/25 rule)
    markets: list[str] = field(default_factory=list)
    type: str = "equity"        # "equity" | "cash"

    def drift(self, actual_pct: float) -> float:
        """Signed drift from target in percentage points."""
        return actual_pct - self.target_pct

    def is_breached(self, actual_pct: float) -> tuple[bool, float]:
        """Check the 5/25 band rule.

        Returns (breached, drift_pp).
        BREACH when:
          abs(drift) > band_abs                 (absolute trigger)
          OR abs(drift) / target > band_rel/100 (relative trigger)
        """
        d = self.drift(actual_pct)
        abs_breach = abs(d) > self.band_abs
        rel_breach = (
            abs(d) / self.target_pct > self.band_rel / 100
            if self.target_pct > 0
            else False
        )
        return abs_breach or rel_breach, d


@dataclass(frozen=True)
class HardLimits:
    max_single_name_pct: float = 20.0
    max_top3_pct: float = 50.0
    max_single_market_pct: float = 70.0
    min_cash_pct: float = 2.0
    max_cash_pct: float = 25.0
    max_drawdown_pct: float = 20.0


@dataclass(frozen=True)
class RebalancingPolicy:
    cadence: str = "monthly"     # annual | quarterly | monthly
    method: str = "hybrid"       # threshold | calendar | hybrid


@dataclass(frozen=True)
class Governance:
    action_description: str = "Review required today"
    action_review_days: int = 0
    warning_description: str = "Review within 7 days"
    warning_review_days: int = 7
    info_description: str = "Monitor — no action needed"


@dataclass(frozen=True)
class Thresholds:
    profit_taking_pct: float = 100.0
    deep_loss_pct: float = -30.0
    critical_loss_pct: float = -50.0
    health_warning: int = 60
    health_critical: int = 40


@dataclass(frozen=True)
class IPSConfig:
    """Complete Investment Policy Statement."""

    version: int
    base_currency: str
    fx_treatment: str
    buckets: list[Bucket]
    hard_limits: HardLimits
    rebalancing: RebalancingPolicy
    governance: Governance
    thresholds: Thresholds

    def equity_buckets(self) -> list[Bucket]:
        return [b for b in self.buckets if b.type != "cash"]

    def cash_bucket(self) -> Bucket | None:
        return next((b for b in self.buckets if b.type == "cash"), None)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _pick_fields(raw: dict, cls: type) -> dict:
    """Filter raw dict to only keys that exist as dataclass fields."""
    valid = {f.name for f in cls.__dataclass_fields__.values()}
    return {k: v for k, v in raw.items() if k in valid}


def load_ips(path: Path | str | None = None) -> IPSConfig:
    """Load IPS config from YAML.  Falls back to built-in defaults."""
    path = Path(path) if path else DEFAULT_IPS_PATH
    log.info("Loading IPS from %s", path)

    with open(path) as f:
        raw = yaml.safe_load(f)

    # --- Buckets ---
    buckets: list[Bucket] = []
    for b in raw.get("allocation", {}).get("buckets", []):
        buckets.append(
            Bucket(
                name=b["name"],
                target_pct=b["target_pct"],
                band_abs=b.get("band_abs", 5.0),
                band_rel=b.get("band_rel", 25.0),
                markets=b.get("markets", []),
                type=b.get("type", "equity"),
            )
        )

    return IPSConfig(
        version=raw.get("version", 1),
        base_currency=raw.get("base_currency", "GBP"),
        fx_treatment=raw.get("fx_treatment", "convert_at_snapshot"),
        buckets=buckets,
        hard_limits=HardLimits(**_pick_fields(raw.get("hard_limits", {}), HardLimits)),
        rebalancing=RebalancingPolicy(
            **_pick_fields(raw.get("rebalancing", {}), RebalancingPolicy)
        ),
        governance=Governance(
            **_pick_fields(raw.get("governance", {}), Governance)
        ),
        thresholds=Thresholds(
            **_pick_fields(raw.get("thresholds", {}), Thresholds)
        ),
    )
