"""IPS-driven policy engine.

Evaluates portfolio metrics against the Investment Policy Statement
and produces typed alerts with governance-level severity.

The LLM never decides what to flag — the IPS and this engine do.

Trigger taxonomy (hybrid model):
  ACTION  — hard constraint breach, drawdown tolerance exceeded,
            5/25 band breach in a major bucket, data integrity issue
  WARNING — near-breach, rising risk, multiple losers deepening,
            cadence-based review due
  INFO    — normal drift within bands, routine performance recap
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from ips import IPSConfig, Bucket
from metrics import PortfolioMetrics, TimeSeriesMetrics

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alert schema
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    ACTION = "🔴 ACTION"
    WARNING = "🟡 WARNING"
    INFO = "🟢 INFO"


@dataclass
class Alert:
    severity: Severity
    category: str       # concentration | drift | cash | drawdown | pnl | health | data | cadence | geographic
    title: str
    detail: str
    rule: str           # which IPS rule produced this (audit trail)
    tickers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bucket drift result
# ---------------------------------------------------------------------------

@dataclass
class BucketDrift:
    """5/25 band evaluation for a single allocation bucket."""

    bucket_name: str
    target_pct: float
    actual_pct: float
    drift_pct: float     # signed
    breached: bool
    band_abs: float
    band_rel: float


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Evaluate portfolio state against IPS rules."""

    def __init__(self, ips: IPSConfig):
        self.ips = ips

    def evaluate(
        self,
        metrics: PortfolioMetrics,
        ts_metrics: TimeSeriesMetrics | None = None,
        last_rebalance_date: str | None = None,
        last_review_date: str | None = None,
    ) -> tuple[list[Alert], list[BucketDrift], bool]:
        """Run all policy checks.

        Returns:
            alerts       — sorted by severity (ACTION first)
            bucket_drifts — 5/25 evaluation for every bucket
            action_required — True if any ACTION-level alert exists
        """
        alerts: list[Alert] = []
        bucket_drifts: list[BucketDrift] = []

        # --- 5/25 band drifts ---
        drifts, drift_alerts = self._check_bucket_drifts(metrics)
        bucket_drifts.extend(drifts)
        alerts.extend(drift_alerts)

        # --- Hard limits ---
        alerts.extend(self._check_concentration(metrics))
        alerts.extend(self._check_cash(metrics))
        alerts.extend(self._check_geographic(metrics))

        # --- Drawdown (requires time-series) ---
        if ts_metrics is not None:
            alerts.extend(self._check_drawdown(ts_metrics))
            alerts.extend(self._check_risk_regime(ts_metrics))

        # --- P/L thresholds ---
        alerts.extend(self._check_profit_taking(metrics))
        alerts.extend(self._check_deep_losses(metrics))

        # --- Health score ---
        alerts.extend(self._check_health(metrics))

        # --- Cadence ---
        alerts.extend(
            self._check_cadence(last_rebalance_date, last_review_date)
        )

        # --- Data integrity ---
        alerts.extend(self._check_data_integrity(metrics, ts_metrics))

        # Sort and determine action_required
        order = {Severity.ACTION: 0, Severity.WARNING: 1, Severity.INFO: 2}
        alerts.sort(key=lambda a: order.get(a.severity, 99))
        action_required = any(a.severity == Severity.ACTION for a in alerts)

        return alerts, bucket_drifts, action_required

    # ------------------------------------------------------------------
    # 5/25 band drift checks
    # ------------------------------------------------------------------

    def _check_bucket_drifts(
        self, m: PortfolioMetrics
    ) -> tuple[list[BucketDrift], list[Alert]]:
        drifts: list[BucketDrift] = []
        alerts: list[Alert] = []

        for bucket in self.ips.buckets:
            actual = self._bucket_actual(bucket, m)

            breached, drift = bucket.is_breached(actual)
            drifts.append(
                BucketDrift(
                    bucket_name=bucket.name,
                    target_pct=bucket.target_pct,
                    actual_pct=round(actual, 1),
                    drift_pct=round(drift, 1),
                    breached=breached,
                    band_abs=bucket.band_abs,
                    band_rel=bucket.band_rel,
                )
            )

            if breached:
                sev = Severity.ACTION if abs(drift) > bucket.band_abs * 1.5 else Severity.WARNING
                alerts.append(
                    Alert(
                        severity=sev,
                        category="drift",
                        title=f"{bucket.name} breaches 5/25 band",
                        detail=(
                            f"Actual {actual:.1f}% vs target {bucket.target_pct:.0f}% "
                            f"(drift {drift:+.1f}pp, band ±{bucket.band_abs}/{bucket.band_rel}%)"
                        ),
                        rule=f"IPS allocation.buckets[{bucket.name}] 5/25 band",
                    )
                )

        return drifts, alerts

    def _bucket_actual(self, bucket: Bucket, m: PortfolioMetrics) -> float:
        """Compute actual weight for a bucket from market weights + cash."""
        if bucket.type == "cash":
            return m.cash_pct

        total = 0.0
        assigned_markets: set[str] = set()
        for b in self.ips.buckets:
            if b.type != "cash":
                assigned_markets.update(b.markets)

        if "*" in bucket.markets:
            # Catch-all: markets not assigned to any other bucket
            for market, weight in m.market_weights.items():
                if market not in assigned_markets or market == "*":
                    total += weight
            # Also include explicitly listed
            for market in bucket.markets:
                if market != "*" and market in m.market_weights:
                    total += m.market_weights[market]
        else:
            for market in bucket.markets:
                total += m.market_weights.get(market, 0)

        return total

    # ------------------------------------------------------------------
    # Hard limits
    # ------------------------------------------------------------------

    def _check_concentration(self, m: PortfolioMetrics) -> list[Alert]:
        alerts: list[Alert] = []
        hl = self.ips.hard_limits

        overweight = [p for p in m.positions if p.weight_pct > hl.max_single_name_pct]
        if overweight:
            alerts.append(
                Alert(
                    Severity.ACTION,
                    "concentration",
                    "Position(s) exceed max weight",
                    f"Above {hl.max_single_name_pct}% limit: "
                    + ", ".join(
                        f"{p.name} ({p.weight_pct:.1f}%)" for p in overweight
                    ),
                    rule=f"IPS hard_limits.max_single_name_pct={hl.max_single_name_pct}",
                    tickers=[p.ticker for p in overweight],
                )
            )

        if m.top3_weight > hl.max_top3_pct:
            top3 = m.positions[:3]
            alerts.append(
                Alert(
                    Severity.WARNING,
                    "concentration",
                    "Top 3 positions too concentrated",
                    f"Top 3 = {m.top3_weight:.1f}% (limit: {hl.max_top3_pct}%): "
                    + ", ".join(
                        f"{p.name} ({p.weight_pct:.1f}%)" for p in top3
                    ),
                    rule=f"IPS hard_limits.max_top3_pct={hl.max_top3_pct}",
                    tickers=[p.ticker for p in top3],
                )
            )
        return alerts

    def _check_cash(self, m: PortfolioMetrics) -> list[Alert]:
        alerts: list[Alert] = []
        hl = self.ips.hard_limits

        if m.cash_pct < hl.min_cash_pct:
            alerts.append(
                Alert(
                    Severity.WARNING,
                    "cash",
                    "Cash buffer low",
                    f"Cash at {m.cash_pct:.1f}% (min: {hl.min_cash_pct:.0f}%). "
                    "Limited capacity for opportunities or margin calls.",
                    rule=f"IPS hard_limits.min_cash_pct={hl.min_cash_pct}",
                )
            )
        elif m.cash_pct > hl.max_cash_pct:
            alerts.append(
                Alert(
                    Severity.INFO,
                    "cash",
                    "High idle cash",
                    f"Cash at {m.cash_pct:.1f}% ({m.free_cash:,.0f} {m.currency}). "
                    "Consider deploying if market conditions warrant.",
                    rule=f"IPS hard_limits.max_cash_pct={hl.max_cash_pct}",
                )
            )
        return alerts

    def _check_geographic(self, m: PortfolioMetrics) -> list[Alert]:
        alerts: list[Alert] = []
        hl = self.ips.hard_limits

        for market, weight in m.market_weights.items():
            if weight > hl.max_single_market_pct and market != "Other":
                alerts.append(
                    Alert(
                        Severity.INFO,
                        "geographic",
                        f"{market} market overweight",
                        f"{market} equities = {weight:.0f}% (limit: {hl.max_single_market_pct:.0f}%). "
                        "Consider geographic diversification.",
                        rule=f"IPS hard_limits.max_single_market_pct={hl.max_single_market_pct}",
                    )
                )
        return alerts

    # ------------------------------------------------------------------
    # Drawdown & risk regime (time-series required)
    # ------------------------------------------------------------------

    def _check_drawdown(self, ts: TimeSeriesMetrics) -> list[Alert]:
        alerts: list[Alert] = []
        max_dd = self.ips.hard_limits.max_drawdown_pct

        if ts.history_days < 30:
            return alerts

        if ts.max_drawdown_pct is not None and abs(ts.max_drawdown_pct) > max_dd:
            alerts.append(
                Alert(
                    Severity.ACTION,
                    "drawdown",
                    "Drawdown exceeds tolerance",
                    f"Max drawdown {ts.max_drawdown_pct:.1f}% exceeds "
                    f"{max_dd}% limit. Current drawdown: {ts.current_drawdown_pct:.1f}%.",
                    rule=f"IPS hard_limits.max_drawdown_pct={max_dd}",
                )
            )
        elif ts.current_drawdown_pct is not None and abs(ts.current_drawdown_pct) > max_dd * 0.7:
            alerts.append(
                Alert(
                    Severity.WARNING,
                    "drawdown",
                    "Drawdown approaching tolerance",
                    f"Current drawdown {ts.current_drawdown_pct:.1f}% is approaching "
                    f"{max_dd}% limit.",
                    rule=f"IPS hard_limits.max_drawdown_pct={max_dd} (70% threshold)",
                )
            )
        return alerts

    def _check_risk_regime(self, ts: TimeSeriesMetrics) -> list[Alert]:
        """Detect regime shifts: volatility spikes, correlation convergence."""
        alerts: list[Alert] = []

        # Volatility spike
        if (
            ts.rolling_30d_vol_pct is not None
            and ts.annual_volatility_pct is not None
            and ts.rolling_30d_vol_pct > ts.annual_volatility_pct * 1.5
        ):
            alerts.append(
                Alert(
                    Severity.WARNING,
                    "risk_regime",
                    "Volatility rising",
                    f"30-day rolling vol {ts.rolling_30d_vol_pct:.1f}% "
                    f"vs annualised {ts.annual_volatility_pct:.1f}% — "
                    "risk regime may be shifting.",
                    rule="Rolling vol > 1.5× annualised vol",
                )
            )

        # Correlation clusters = hidden concentration
        if ts.correlation_clusters:
            n_clusters = len(ts.correlation_clusters)
            cluster_desc = "; ".join(
                ", ".join(c[:3]) + ("…" if len(c) > 3 else "")
                for c in ts.correlation_clusters[:3]
            )
            alerts.append(
                Alert(
                    Severity.WARNING,
                    "risk_regime",
                    "Correlation clusters detected",
                    f"{n_clusters} group(s) of highly correlated positions: {cluster_desc}. "
                    "This is hidden concentration — diversification benefit is reduced.",
                    rule="Pairwise return correlation ≥ 0.75",
                )
            )

        return alerts

    # ------------------------------------------------------------------
    # P/L thresholds
    # ------------------------------------------------------------------

    def _check_profit_taking(self, m: PortfolioMetrics) -> list[Alert]:
        threshold = self.ips.thresholds.profit_taking_pct
        candidates = [p for p in m.winners if p.pnl_pct >= threshold]
        if not candidates:
            return []

        return [
            Alert(
                Severity.WARNING,
                "profit_taking",
                "Profit-taking candidates",
                f"Positions with >{threshold:.0f}% gain — review for trimming:\n"
                + "\n".join(
                    f"  • {p.name}: +{p.pnl_pct:.0f}% ({p.pnl:+,.2f})"
                    for p in candidates
                ),
                rule=f"IPS thresholds.profit_taking_pct={threshold}",
                tickers=[p.ticker for p in candidates],
            )
        ]

    def _check_deep_losses(self, m: PortfolioMetrics) -> list[Alert]:
        deep_thr = self.ips.thresholds.deep_loss_pct
        crit_thr = self.ips.thresholds.critical_loss_pct
        deep = [p for p in m.losers if p.pnl_pct <= deep_thr]
        if not deep:
            return []

        severity = (
            Severity.ACTION
            if any(p.pnl_pct <= crit_thr for p in deep)
            else Severity.WARNING
        )
        return [
            Alert(
                severity,
                "loss_review",
                "Deep-loss review",
                "Positions with significant losses — re-evaluate thesis:\n"
                + "\n".join(
                    f"  • {p.name}: {p.pnl_pct:.0f}% ({p.pnl:+,.2f})"
                    for p in deep
                ),
                rule=f"IPS thresholds.deep_loss_pct={deep_thr}, critical={crit_thr}",
                tickers=[p.ticker for p in deep],
            )
        ]

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def _check_health(self, m: PortfolioMetrics) -> list[Alert]:
        alerts: list[Alert] = []
        sub = ", ".join(f"{k}={v}/25" for k, v in m.health_sub.items())
        warn = self.ips.thresholds.health_warning
        crit = self.ips.thresholds.health_critical

        if m.health_score < crit:
            alerts.append(
                Alert(
                    Severity.ACTION,
                    "health",
                    "Portfolio health critical",
                    f"Score {m.health_score}/100 ({sub}). Immediate review recommended.",
                    rule=f"IPS thresholds.health_critical={crit}",
                )
            )
        elif m.health_score < warn:
            alerts.append(
                Alert(
                    Severity.WARNING,
                    "health",
                    "Portfolio health below target",
                    f"Score {m.health_score}/100 ({sub}). Monitor for deterioration.",
                    rule=f"IPS thresholds.health_warning={warn}",
                )
            )
        return alerts

    # ------------------------------------------------------------------
    # Cadence
    # ------------------------------------------------------------------

    _CADENCE_DAYS = {"monthly": 30, "quarterly": 91, "annual": 365}

    def _check_cadence(
        self,
        last_rebalance: str | None,
        last_review: str | None,
    ) -> list[Alert]:
        """Check if a scheduled review/rebalance is due."""
        if self.ips.rebalancing.method not in ("calendar", "hybrid"):
            return []

        cadence_days = self._CADENCE_DAYS.get(
            self.ips.rebalancing.cadence, 30
        )
        today = date.today()
        alerts: list[Alert] = []

        if last_rebalance:
            try:
                last = date.fromisoformat(last_rebalance)
                days_since = (today - last).days
                if days_since >= cadence_days:
                    alerts.append(
                        Alert(
                            Severity.WARNING,
                            "cadence",
                            f"Scheduled {self.ips.rebalancing.cadence} rebalance due",
                            f"Last rebalance: {last_rebalance} ({days_since} days ago). "
                            f"Cadence: {self.ips.rebalancing.cadence} ({cadence_days}d).",
                            rule=f"IPS rebalancing.cadence={self.ips.rebalancing.cadence}",
                        )
                    )
            except ValueError:
                pass
        else:
            # No rebalance recorded — flag as info on first run
            alerts.append(
                Alert(
                    Severity.INFO,
                    "cadence",
                    "No rebalance history",
                    "No previous rebalance recorded. "
                    "Consider establishing a baseline allocation.",
                    rule="IPS rebalancing (first run)",
                )
            )

        return alerts

    # ------------------------------------------------------------------
    # Data integrity
    # ------------------------------------------------------------------

    def _check_data_integrity(
        self,
        m: PortfolioMetrics,
        ts: TimeSeriesMetrics | None,
    ) -> list[Alert]:
        """Flag missing/stale data that invalidates metrics."""
        alerts: list[Alert] = []

        # Missing prices
        zero_price = [p for p in m.positions if p.current_price <= 0]
        if zero_price:
            alerts.append(
                Alert(
                    Severity.ACTION,
                    "data",
                    "Missing price data",
                    f"{len(zero_price)} position(s) with zero/missing prices — "
                    "metrics may be inaccurate: "
                    + ", ".join(p.name for p in zero_price[:5]),
                    rule="Data integrity: all positions must have valid price",
                    tickers=[p.ticker for p in zero_price],
                )
            )

        # Zero total value
        if m.total_value <= 0:
            alerts.append(
                Alert(
                    Severity.ACTION,
                    "data",
                    "Invalid portfolio value",
                    "Total portfolio value is zero or negative — "
                    "API data may be stale or corrupted.",
                    rule="Data integrity: total_value > 0",
                )
            )

        # Insufficient history warning
        if ts is None:
            alerts.append(
                Alert(
                    Severity.INFO,
                    "data",
                    "Limited history",
                    "Fewer than 5 daily snapshots stored — "
                    "time-series metrics (drawdown, volatility, Sharpe) unavailable. "
                    "These will become available as history accumulates.",
                    rule="Time-series requires ≥ 5 snapshots",
                )
            )

        return alerts
