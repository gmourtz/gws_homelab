"""Rebalance candidate generation (read-only — never places orders).

When policy says "rebalance suggested," generates 2–3 deterministic
candidates.  The agent presents options + trade-offs, not a single
"do this" command.

Candidates:
  1. Policy rebalance  — min trades to get back within IPS bands
  2. Risk-reduction    — reduce concentration / HRP tilt (needs history)
  3. Do-nothing        — explicit monitor plan with next trigger

Uses scipy for hierarchical risk parity when sufficient return history
exists.  Falls back to equal-weight-within-bucket otherwise.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

try:
    from scipy.cluster.hierarchy import linkage, leaves_list
    from scipy.spatial.distance import squareform

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False
    log.info("scipy not available — HRP optimisation disabled")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TradeProposal:
    """A single proposed trade (read-only suggestion)."""

    ticker: str
    name: str
    direction: str              # "BUY" | "SELL" | "TRIM" | "ADD"
    current_weight_pct: float
    target_weight_pct: float
    delta_pct: float            # positive = buy, negative = sell
    rationale: str


@dataclass
class RebalanceCandidate:
    """One of 2–3 candidate rebalancing plans."""

    name: str
    description: str
    trades: list[TradeProposal]
    pros: list[str]
    cons: list[str]
    policy_impact: str          # effect on IPS compliance
    estimated_turnover_pct: float  # sum of |delta| / 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_rebalance_options(
    current_weights: dict[str, float],       # ticker → weight %
    bucket_assignments: dict[str, str],      # ticker → bucket name
    bucket_targets: dict[str, float],        # bucket name → target %
    cash_pct: float,
    cash_target_pct: float,
    position_names: dict[str, str],          # ticker → display name
    price_history: pd.DataFrame | None = None,
    breached_buckets: list[str] | None = None,
) -> list[RebalanceCandidate]:
    """Generate 2–3 deterministic rebalance candidates.

    All calculations are read-only.  The agent never places orders.
    """
    candidates: list[RebalanceCandidate] = []

    # 1. Policy rebalance (always available)
    policy_rb = _policy_rebalance(
        current_weights,
        bucket_assignments,
        bucket_targets,
        cash_pct,
        cash_target_pct,
        position_names,
    )
    candidates.append(policy_rb)

    # 2. Risk-reduction rebalance (needs return history)
    if price_history is not None and _HAS_SCIPY and price_history.shape[0] >= 30:
        risk_rb = _risk_reduction_rebalance(
            current_weights,
            price_history,
            position_names,
        )
        if risk_rb is not None:
            candidates.append(risk_rb)

    # 3. Do-nothing plan (always available)
    candidates.append(
        _do_nothing_plan(breached_buckets or [], cash_pct, cash_target_pct)
    )

    return candidates


# ---------------------------------------------------------------------------
# Candidate 1: Policy rebalance (min trades to IPS compliance)
# ---------------------------------------------------------------------------

def _policy_rebalance(
    current_weights: dict[str, float],
    bucket_assignments: dict[str, str],
    bucket_targets: dict[str, float],
    cash_pct: float,
    cash_target_pct: float,
    position_names: dict[str, str],
) -> RebalanceCandidate:
    """Minimum-trade rebalance to get back within IPS bands."""

    # Aggregate current weights per bucket
    bucket_actuals: dict[str, float] = {}
    for ticker, bucket in bucket_assignments.items():
        bucket_actuals[bucket] = (
            bucket_actuals.get(bucket, 0) + current_weights.get(ticker, 0)
        )

    # Compute per-bucket deltas
    bucket_deltas: dict[str, float] = {}
    for bname, target in bucket_targets.items():
        actual = bucket_actuals.get(bname, 0)
        bucket_deltas[bname] = target - actual

    # Distribute bucket-level delta pro-rata to positions
    trades: list[TradeProposal] = []
    for ticker, bucket in bucket_assignments.items():
        w = current_weights.get(ticker, 0)
        delta = bucket_deltas.get(bucket, 0)
        if abs(delta) < 0.5:
            continue

        bucket_total = bucket_actuals.get(bucket, 0) or 1
        pos_delta = delta * (w / bucket_total) if bucket_total > 0 else 0

        if abs(pos_delta) < 0.3:
            continue

        direction = "BUY" if pos_delta > 0 else "SELL"
        if 0 < pos_delta < 2:
            direction = "ADD"
        elif -2 < pos_delta < 0:
            direction = "TRIM"

        trades.append(
            TradeProposal(
                ticker=ticker,
                name=position_names.get(ticker, ticker),
                direction=direction,
                current_weight_pct=round(w, 1),
                target_weight_pct=round(w + pos_delta, 1),
                delta_pct=round(pos_delta, 1),
                rationale=f"Bring {bucket} bucket closer to {bucket_targets.get(bucket, 0):.0f}% target",
            )
        )

    turnover = sum(abs(t.delta_pct) for t in trades) / 2

    return RebalanceCandidate(
        name="Policy rebalance",
        description="Minimum trades to bring all IPS buckets within bands",
        trades=sorted(trades, key=lambda t: abs(t.delta_pct), reverse=True),
        pros=[
            "Returns portfolio to IPS-compliant allocation",
            "Lowest transaction cost of rebalance options",
        ],
        cons=[
            "Does not account for risk-adjusted positioning",
            "May trade against current momentum",
        ],
        policy_impact="All bucket drifts brought within 5/25 bands",
        estimated_turnover_pct=round(turnover, 1),
    )


# ---------------------------------------------------------------------------
# Candidate 2: Risk-reduction rebalance (HRP-inspired)
# ---------------------------------------------------------------------------

def _risk_reduction_rebalance(
    current_weights: dict[str, float],
    price_history: pd.DataFrame,
    position_names: dict[str, str],
) -> RebalanceCandidate | None:
    """Generate risk-parity-inspired target weights via HRP."""
    try:
        # Compute returns for tickers we hold
        held = [t for t in current_weights if t in price_history.columns]
        if len(held) < 3:
            return None

        prices = price_history[held].dropna(how="all")
        returns = prices.pct_change().dropna()
        if len(returns) < 20:
            return None

        # HRP weights
        hrp_weights = _hrp_allocate(returns)
        if hrp_weights is None:
            return None

        trades: list[TradeProposal] = []
        for ticker in held:
            curr_w = current_weights.get(ticker, 0)
            target_w = hrp_weights.get(ticker, 0) * 100  # convert to %
            delta = target_w - curr_w

            if abs(delta) < 0.5:
                continue

            direction = "ADD" if delta > 0 else "TRIM"
            if delta > 3:
                direction = "BUY"
            elif delta < -3:
                direction = "SELL"

            trades.append(
                TradeProposal(
                    ticker=ticker,
                    name=position_names.get(ticker, ticker),
                    direction=direction,
                    current_weight_pct=round(curr_w, 1),
                    target_weight_pct=round(target_w, 1),
                    delta_pct=round(delta, 1),
                    rationale="Risk-parity tilt to reduce concentration risk",
                )
            )

        if not trades:
            return None

        turnover = sum(abs(t.delta_pct) for t in trades) / 2

        return RebalanceCandidate(
            name="Risk-reduction rebalance",
            description=(
                "Hierarchical risk parity (HRP) allocation — "
                "reduces concentration by weighting inversely to correlated risk"
            ),
            trades=sorted(trades, key=lambda t: abs(t.delta_pct), reverse=True),
            pros=[
                "Reduces portfolio risk without requiring return forecasts",
                "Diversifies across correlation clusters",
                "Robust to estimation error (no covariance matrix inversion)",
            ],
            cons=[
                "Higher turnover than policy rebalance",
                "May underweight high-conviction positions",
                "Based on historical correlations (backward-looking)",
            ],
            policy_impact="Concentration risk reduced; may not match IPS bucket targets exactly",
            estimated_turnover_pct=round(turnover, 1),
        )
    except Exception as e:
        log.warning("Risk-reduction rebalance failed: %s", e)
        return None


def _hrp_allocate(returns: pd.DataFrame) -> dict[str, float] | None:
    """Hierarchical Risk Parity allocation.

    Implements López de Prado's HRP:
      1. Compute correlation-based distance
      2. Hierarchical clustering (Ward linkage)
      3. Quasi-diagonal reordering
      4. Recursive bisection with inverse-variance weighting
    """
    try:
        cov = returns.cov()
        corr = returns.corr()
        tickers = list(corr.columns)
        n = len(tickers)

        if n < 3:
            return None

        # Distance matrix from correlation
        dist = np.sqrt(0.5 * (1 - corr.values))
        np.fill_diagonal(dist, 0)
        condensed = squareform(dist, checks=False)
        condensed = np.nan_to_num(condensed, nan=1.0)

        # Hierarchical clustering
        link = linkage(condensed, method="ward")
        order = list(leaves_list(link))
        ordered_tickers = [tickers[i] for i in order]

        # Recursive bisection
        weights = _recursive_bisect(cov, ordered_tickers)
        return weights
    except Exception as e:
        log.warning("HRP allocation failed: %s", e)
        return None


def _recursive_bisect(
    cov: pd.DataFrame,
    ordered_tickers: list[str],
) -> dict[str, float]:
    """Recursive bisection step of HRP."""
    weights: dict[str, float] = {t: 1.0 for t in ordered_tickers}

    clusters = [ordered_tickers]
    while clusters:
        next_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left = cluster[:mid]
            right = cluster[mid:]

            # Inverse-variance allocation between left and right
            var_left = _cluster_var(cov, left)
            var_right = _cluster_var(cov, right)

            alpha = 1 - var_left / (var_left + var_right) if (var_left + var_right) > 0 else 0.5

            for t in left:
                weights[t] *= alpha
            for t in right:
                weights[t] *= (1 - alpha)

            if len(left) > 1:
                next_clusters.append(left)
            if len(right) > 1:
                next_clusters.append(right)

        clusters = next_clusters

    # Normalise
    total = sum(weights.values())
    if total > 0:
        weights = {t: w / total for t, w in weights.items()}

    return weights


def _cluster_var(cov: pd.DataFrame, tickers: list[str]) -> float:
    """Inverse-variance portfolio variance for a cluster."""
    sub_cov = cov.loc[tickers, tickers]
    ivp = 1.0 / np.diag(sub_cov.values)
    ivp /= ivp.sum()
    return float(ivp @ sub_cov.values @ ivp)


# ---------------------------------------------------------------------------
# Candidate 3: Do-nothing plan
# ---------------------------------------------------------------------------

def _do_nothing_plan(
    breached_buckets: list[str],
    cash_pct: float,
    cash_target_pct: float,
) -> RebalanceCandidate:
    """Explicit monitor plan — what triggers the next action."""
    triggers: list[str] = []
    if breached_buckets:
        triggers.append(
            f"Bucket drift worsens: {', '.join(breached_buckets)}"
        )
    triggers.append("Any hard limit breached (concentration, cash, drawdown)")
    triggers.append("Next scheduled rebalancing date")
    triggers.append("Significant market event affecting top holdings")

    return RebalanceCandidate(
        name="Do nothing — monitor",
        description="No trades.  Continue holding; review at next scheduled check.",
        trades=[],
        pros=[
            "Zero transaction costs",
            "Avoids over-trading (reduces tax drag and slippage)",
            "Preserves current momentum if market trend is favourable",
        ],
        cons=[
            "Drift may increase if current trends continue",
            f"Cash at {cash_pct:.1f}% vs {cash_target_pct:.0f}% target"
            if abs(cash_pct - cash_target_pct) > 2
            else "Current allocation acceptable",
        ],
        policy_impact="No change to IPS compliance; triggers for escalation: "
        + "; ".join(triggers[:3]),
        estimated_turnover_pct=0.0,
    )
