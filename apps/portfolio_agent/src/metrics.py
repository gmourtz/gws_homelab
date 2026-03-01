"""Deterministic portfolio metrics — snapshot + time-series + per-stock.

All analytics are computed in code — no LLM involved.
The LLM only sees the output of this module, never raw API data.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

from tickers import parse_ticker


# ---------------------------------------------------------------------------
# Snapshot-based data classes
# ---------------------------------------------------------------------------

@dataclass
class PositionMetric:
    """Pre-computed metrics for a single position."""

    ticker: str
    name: str
    market: str
    quantity: float
    avg_price: float
    current_price: float
    current_value: float
    weight_pct: float
    pnl: float
    pnl_pct: float
    fx_impact: float


@dataclass
class PortfolioMetrics:
    """Complete set of deterministic portfolio analytics."""

    timestamp: str
    currency: str

    # Totals
    total_value: float
    total_invested: float
    free_cash: float
    cash_pct: float
    overall_pnl: float
    overall_pnl_pct: float
    realized_pnl: float
    num_positions: int

    # Concentration
    hhi: float
    top1_weight: float
    top1_ticker: str
    top3_weight: float
    top5_weight: float
    market_weights: dict[str, float]

    # Positions (sorted by weight desc)
    positions: list[PositionMetric]

    # Health
    health_score: int
    health_sub: dict[str, int]

    # Convenience
    winners: list[PositionMetric] = field(default_factory=list)
    losers: list[PositionMetric] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time-series data class
# ---------------------------------------------------------------------------

@dataclass
class TimeSeriesMetrics:
    """Risk / return metrics derived from stored daily snapshots."""

    history_days: int
    annual_return_pct: float | None
    annual_volatility_pct: float | None
    sharpe_ratio: float | None          # rf = 0.04 (GBP base)
    sortino_ratio: float | None
    max_drawdown_pct: float | None
    current_drawdown_pct: float | None
    rolling_30d_vol_pct: float | None
    calmar_ratio: float | None          # annual_return / max_drawdown
    correlation_clusters: list[list[str]] | None   # groups moving together


# ---------------------------------------------------------------------------
# Per-stock scoring
# ---------------------------------------------------------------------------

@dataclass
class StockScore:
    """Per-stock analysis combining position data, fundamentals, and context."""

    ticker: str
    name: str
    sector: str

    # Position context
    weight_pct: float
    pnl_pct: float
    current_value: float

    # Fundamentals (None = unavailable)
    pe_ratio: float | None
    eps_growth_pct: float | None
    rev_growth_pct: float | None
    debt_to_equity: float | None
    roe_pct: float | None
    dividend_yield_pct: float | None
    beta: float | None
    net_margin_pct: float | None

    # Technical context
    pct_from_52w_high: float | None
    pct_from_52w_low: float | None

    # Scores
    fundamental_score: int       # 0–100 (-1 if insufficient data)
    valuation: str               # CHEAP | FAIR | EXPENSIVE | UNKNOWN

    # Flags
    earnings_soon: bool
    has_negative_growth: bool
    high_leverage: bool
    near_52w_high: bool
    near_52w_low: bool


# ===================================================================
# Snapshot metrics
# ===================================================================

def compute_metrics(snapshot: dict) -> PortfolioMetrics:
    """Compute all portfolio metrics from a Trading 212 snapshot."""
    cash_data = snapshot["cash"]
    account = snapshot["account"]
    raw_positions = snapshot["positions"]

    total_invested = cash_data.get("invested", 0)
    free_cash = cash_data.get("free", 0)
    overall_pnl = cash_data.get("ppl", 0)
    realized_pnl = cash_data.get("realizedPpl", 0)
    total_value = cash_data.get("totalValue", 0) or (
        total_invested + overall_pnl + free_cash
    )

    # --- Build position metrics ---
    positions: list[PositionMetric] = []
    for p in raw_positions:
        instrument = p.get("instrument", {})
        ticker_raw = instrument.get("ticker", "UNKNOWN")
        symbol, market = parse_ticker(ticker_raw)

        qty = p.get("quantity", 0)
        avg = p.get("averagePricePaid", 0)
        curr = p.get("currentPrice", 0)
        wallet = p.get("walletImpact") or {}
        pos_pnl = wallet.get("unrealizedProfitLoss") or 0
        fx = wallet.get("fxImpact") or 0
        pos_value = wallet.get("currentValue") or (qty * curr)

        weight = (pos_value / total_value * 100) if total_value else 0
        pnl_pct = ((curr - avg) / avg * 100) if avg else 0

        positions.append(
            PositionMetric(
                ticker=ticker_raw,
                name=instrument.get("name", symbol),
                market=market,
                quantity=qty,
                avg_price=avg,
                current_price=curr,
                current_value=pos_value,
                weight_pct=weight,
                pnl=pos_pnl,
                pnl_pct=pnl_pct,
                fx_impact=fx,
            )
        )

    positions.sort(key=lambda p: p.weight_pct, reverse=True)

    # --- Concentration ---
    weights = [p.weight_pct for p in positions]
    hhi = sum(w**2 for w in weights)
    top1_weight = weights[0] if weights else 0
    top1_ticker = positions[0].ticker if positions else ""
    top3_weight = sum(weights[:3])
    top5_weight = sum(weights[:5])

    market_weights: dict[str, float] = {}
    for p in positions:
        market_weights[p.market] = market_weights.get(p.market, 0) + p.weight_pct

    # --- Winners / Losers ---
    winners = sorted(
        [p for p in positions if p.pnl_pct > 0],
        key=lambda p: p.pnl_pct,
        reverse=True,
    )
    losers = sorted(
        [p for p in positions if p.pnl_pct < 0],
        key=lambda p: p.pnl_pct,
    )

    # --- Derived ---
    cash_pct = (free_cash / total_value * 100) if total_value else 0
    overall_pnl_pct = (overall_pnl / total_invested * 100) if total_invested else 0

    # --- Health score ---
    health_sub = _health_scores(
        hhi=hhi,
        top1_weight=top1_weight,
        cash_pct=cash_pct,
        positions=positions,
        overall_pnl_pct=overall_pnl_pct,
        winners=winners,
        losers=losers,
    )
    health_score = sum(health_sub.values())

    return PortfolioMetrics(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        currency=account.get("currency", "Unknown"),
        total_value=total_value,
        total_invested=total_invested,
        free_cash=free_cash,
        cash_pct=cash_pct,
        overall_pnl=overall_pnl,
        overall_pnl_pct=overall_pnl_pct,
        realized_pnl=realized_pnl,
        num_positions=len(positions),
        hhi=hhi,
        top1_weight=top1_weight,
        top1_ticker=top1_ticker,
        top3_weight=top3_weight,
        top5_weight=top5_weight,
        market_weights=market_weights,
        positions=positions,
        health_score=health_score,
        health_sub=health_sub,
        winners=winners,
        losers=losers,
    )


# ===================================================================
# Time-series metrics (requires stored snapshots)
# ===================================================================

RISK_FREE_RATE = 0.04   # GBP base, ~4 % p.a.


def compute_timeseries_metrics(
    returns: pd.Series | None,
    values: pd.Series | None,
    price_history: pd.DataFrame | None,
) -> TimeSeriesMetrics | None:
    """Compute risk/return analytics from stored daily data.

    Returns None if insufficient history (< 5 data points).
    """
    if returns is None or len(returns) < 5:
        return None

    n = len(returns)
    history_days = (returns.index[-1] - returns.index[0]).days or 1

    # --- Annualised return ---
    total_return = (1 + returns).prod() - 1
    ann_factor = 365.25 / history_days
    ann_return = (1 + total_return) ** ann_factor - 1 if history_days > 0 else None

    # --- Annualised volatility ---
    daily_vol = returns.std()
    ann_vol = daily_vol * math.sqrt(252) if daily_vol else None

    # --- Sharpe ---
    sharpe = (
        (ann_return - RISK_FREE_RATE) / ann_vol
        if ann_return is not None and ann_vol and ann_vol > 0
        else None
    )

    # --- Sortino (downside deviation) ---
    downside = returns[returns < 0]
    downside_dev = downside.std() * math.sqrt(252) if len(downside) > 1 else None
    sortino = (
        (ann_return - RISK_FREE_RATE) / downside_dev
        if ann_return is not None and downside_dev and downside_dev > 0
        else None
    )

    # --- Drawdown ---
    max_dd = None
    current_dd = None
    if values is not None and len(values) >= 2:
        cummax = values.cummax()
        dd = (values - cummax) / cummax
        max_dd = float(dd.min()) * 100
        current_dd = float(dd.iloc[-1]) * 100

    # --- Rolling 30-day volatility ---
    rolling_vol = None
    if n >= 30:
        r30 = returns.rolling(30).std().dropna()
        if len(r30) > 0:
            rolling_vol = float(r30.iloc[-1]) * math.sqrt(252) * 100

    # --- Calmar ---
    calmar = None
    if ann_return is not None and max_dd is not None and max_dd < 0:
        calmar = ann_return / abs(max_dd / 100)

    # --- Correlation clusters ---
    clusters = _correlation_clusters(price_history)

    return TimeSeriesMetrics(
        history_days=history_days,
        annual_return_pct=_pct(ann_return),
        annual_volatility_pct=_pct(ann_vol),
        sharpe_ratio=_rnd(sharpe),
        sortino_ratio=_rnd(sortino),
        max_drawdown_pct=_rnd(max_dd),
        current_drawdown_pct=_rnd(current_dd),
        rolling_30d_vol_pct=_rnd(rolling_vol),
        calmar_ratio=_rnd(calmar),
        correlation_clusters=clusters,
    )


def _correlation_clusters(
    price_history: pd.DataFrame | None,
    threshold: float = 0.75,
) -> list[list[str]] | None:
    """Find ticker groups with correlation ≥ threshold (hidden concentration)."""
    if price_history is None or price_history.shape[1] < 3:
        return None

    try:
        rets = price_history.pct_change().dropna()
        if len(rets) < 10:
            return None

        valid = rets.columns[rets.std() > 1e-9]
        if len(valid) < 3:
            return None
        rets = rets[valid]
        corr = rets.corr()

        # Union-find clustering
        parent: dict[str, str] = {c: c for c in corr.columns}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            parent[find(a)] = find(b)

        cols = list(corr.columns)
        for i, a in enumerate(cols):
            for b in cols[i + 1 :]:
                if corr.loc[a, b] >= threshold:
                    union(a, b)

        groups: dict[str, list[str]] = {}
        for c in cols:
            root = find(c)
            groups.setdefault(root, []).append(c)

        clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
        return clusters or None
    except Exception as e:
        log.warning("Correlation clustering failed: %s", e)
        return None


def _pct(val: float | None) -> float | None:
    return round(val * 100, 2) if val is not None else None


def _rnd(val: float | None) -> float | None:
    return round(val, 2) if val is not None else None


# ===================================================================
# Health sub-scores (each 0–25, total 0–100)
# ===================================================================

def _health_scores(
    *,
    hhi: float,
    top1_weight: float,
    cash_pct: float,
    positions: list[PositionMetric],
    overall_pnl_pct: float,
    winners: list[PositionMetric],
    losers: list[PositionMetric],
) -> dict[str, int]:
    # Diversification (0-25)
    div = max(0, min(25, int(25 * (1 - max(0, hhi - 400) / 2100))))
    if top1_weight > 20:
        div = max(0, div - 10)
    elif top1_weight > 15:
        div = max(0, div - 5)

    # Risk balance (0-25)
    if positions:
        deep_loss_ratio = sum(1 for p in positions if p.pnl_pct < -20) / len(
            positions
        )
        risk = max(0, min(25, int(25 * (1 - deep_loss_ratio / 0.4))))
    else:
        risk = 25

    # Cash buffer (0-25)
    if cash_pct < 1:
        cash = 5
    elif cash_pct < 3:
        cash = 15
    elif cash_pct <= 10:
        cash = 25
    elif cash_pct <= 20:
        cash = 20
    elif cash_pct <= 30:
        cash = 15
    else:
        cash = 10

    # Momentum (0-25)
    if positions:
        win_ratio = len(winners) / len(positions)
        mom = int(15 * win_ratio)
        if overall_pnl_pct > 10:
            mom += 10
        elif overall_pnl_pct > 0:
            mom += 5
        elif overall_pnl_pct > -5:
            mom += 2
        momentum = min(25, mom)
    else:
        momentum = 12

    return {
        "diversification": div,
        "risk": risk,
        "cash": cash,
        "momentum": momentum,
    }


# ===================================================================
# Per-stock scoring
# ===================================================================

def score_stocks(
    positions: list[PositionMetric],
    fundamentals: dict[str, dict | None],
    profiles: dict[str, dict | None],
    earnings_calendar: dict[str, str],
    symbol_map: dict[str, str],
) -> list[StockScore]:
    """Score every position using fundamentals + technical context."""
    scores: list[StockScore] = []
    for pos in positions:
        fh_sym = symbol_map.get(pos.ticker, "")
        fund = fundamentals.get(fh_sym) or {}
        prof = profiles.get(fh_sym) or {}

        sector = prof.get("sector", "Unknown")
        pe = fund.get("pe")
        eps_g = fund.get("eps_growth")
        rev_g = fund.get("rev_growth")
        de = fund.get("debt_to_equity")
        roe = fund.get("roe")
        div_y = fund.get("div_yield")
        beta = fund.get("beta")
        net_m = fund.get("net_margin")
        w52h = fund.get("w52_high")
        w52l = fund.get("w52_low")

        pct_from_high = pct_from_low = None
        if w52h and w52h > 0:
            pct_from_high = (pos.current_price - w52h) / w52h * 100
        if w52l and w52l > 0:
            pct_from_low = (pos.current_price - w52l) / w52l * 100

        near_high = pct_from_high is not None and pct_from_high > -5
        near_low = pct_from_low is not None and pct_from_low < 10

        has_neg_growth = (eps_g is not None and eps_g < 0) or (
            rev_g is not None and rev_g < 0
        )
        high_lev = de is not None and de > 2.0

        fscore = _fundamental_score(pe, eps_g, rev_g, de, roe, net_m)
        val = _valuation_signal(pe, eps_g, rev_g)
        earnings_soon = fh_sym in earnings_calendar

        scores.append(
            StockScore(
                ticker=pos.ticker,
                name=pos.name,
                sector=sector,
                weight_pct=pos.weight_pct,
                pnl_pct=pos.pnl_pct,
                current_value=pos.current_value,
                pe_ratio=pe,
                eps_growth_pct=eps_g,
                rev_growth_pct=rev_g,
                debt_to_equity=de,
                roe_pct=roe,
                dividend_yield_pct=div_y,
                beta=beta,
                net_margin_pct=net_m,
                pct_from_52w_high=pct_from_high,
                pct_from_52w_low=pct_from_low,
                fundamental_score=fscore,
                valuation=val,
                earnings_soon=earnings_soon,
                has_negative_growth=has_neg_growth,
                high_leverage=high_lev,
                near_52w_high=near_high,
                near_52w_low=near_low,
            )
        )
    return scores


def _fundamental_score(
    pe: float | None,
    eps_growth: float | None,
    rev_growth: float | None,
    debt_to_equity: float | None,
    roe: float | None,
    net_margin: float | None,
) -> int:
    """0–100 fundamental score.  Returns -1 if < 2 metrics available."""
    components: list[float] = []

    # Growth (0–30)
    growth_pts = 0.0
    growth_avail = 0
    if eps_growth is not None:
        growth_avail += 1
        if eps_growth > 25:
            growth_pts += 15
        elif eps_growth > 10:
            growth_pts += 12
        elif eps_growth > 0:
            growth_pts += 8
        elif eps_growth > -10:
            growth_pts += 4
    if rev_growth is not None:
        growth_avail += 1
        if rev_growth > 20:
            growth_pts += 15
        elif rev_growth > 10:
            growth_pts += 12
        elif rev_growth > 0:
            growth_pts += 8
        elif rev_growth > -10:
            growth_pts += 4
    if growth_avail > 0:
        components.append(growth_pts)

    # Profitability (0–25)
    profit_pts = 0.0
    profit_avail = 0
    if roe is not None:
        profit_avail += 1
        if roe > 20:
            profit_pts += 13
        elif roe > 10:
            profit_pts += 10
        elif roe > 0:
            profit_pts += 5
    if net_margin is not None:
        profit_avail += 1
        if net_margin > 20:
            profit_pts += 12
        elif net_margin > 10:
            profit_pts += 9
        elif net_margin > 0:
            profit_pts += 5
    if profit_avail > 0:
        components.append(profit_pts)

    # Valuation (0–25)
    if pe is not None:
        if pe < 0:
            val_pts = 2.0
        elif pe < 12:
            val_pts = 25.0
        elif pe < 20:
            val_pts = 20.0
        elif pe < 30:
            val_pts = 15.0
        elif pe < 50:
            val_pts = 8.0
        else:
            val_pts = 3.0
        components.append(val_pts)

    # Balance sheet (0–20)
    if debt_to_equity is not None:
        if debt_to_equity < 0.3:
            bs_pts = 20.0
        elif debt_to_equity < 0.7:
            bs_pts = 16.0
        elif debt_to_equity < 1.5:
            bs_pts = 12.0
        elif debt_to_equity < 3.0:
            bs_pts = 6.0
        else:
            bs_pts = 2.0
        components.append(bs_pts)

    if len(components) < 2:
        return -1
    return min(100, int(sum(components)))


def _valuation_signal(
    pe: float | None,
    eps_growth: float | None,
    rev_growth: float | None,
) -> str:
    """CHEAP / FAIR / EXPENSIVE / UNKNOWN."""
    if pe is None or pe < 0:
        return "UNKNOWN"

    growth = eps_growth if eps_growth is not None else rev_growth
    if growth is None:
        if pe < 12:
            return "CHEAP"
        elif pe < 25:
            return "FAIR"
        else:
            return "EXPENSIVE"

    if growth <= 0:
        return "EXPENSIVE" if pe > 15 else "FAIR"

    peg = pe / growth
    if peg < 1.0:
        return "CHEAP"
    elif peg < 2.0:
        return "FAIR"
    else:
        return "EXPENSIVE"


# ===================================================================
# DCA / accumulation signals
# ===================================================================

@dataclass
class DCASignal:
    """Moving-average accumulation signal for one position."""

    ticker: str
    name: str
    current_price: float
    sma_30: float | None
    sma_50: float | None
    weight_pct: float
    pnl_pct: float
    signal: str  # "BUY_ZONE" | "NEUTRAL" | "EXTENDED"


def compute_dca_signals(
    positions: list[PositionMetric],
    price_history: pd.DataFrame | None,
) -> list[DCASignal]:
    """Identify DCA opportunities using simple moving averages.

    BUY_ZONE:  price < SMA-30 (short-term pullback in a position you own).
    EXTENDED:  price > 1.15 × SMA-50 (over-extended, consider pausing DCA).
    NEUTRAL:   everything else.

    Returns only positions with at least 20 days of price history.
    """
    if price_history is None or len(price_history) < 20:
        return []

    signals: list[DCASignal] = []
    for pos in positions:
        if pos.ticker not in price_history.columns:
            continue

        prices = price_history[pos.ticker].dropna()
        if len(prices) < 20:
            continue

        sma_30 = float(prices.tail(30).mean()) if len(prices) >= 30 else None
        sma_50 = float(prices.tail(50).mean()) if len(prices) >= 50 else None
        current = pos.current_price

        if sma_30 is not None and current < sma_30:
            signal = "BUY_ZONE"
        elif sma_50 is not None and current > sma_50 * 1.15:
            signal = "EXTENDED"
        else:
            signal = "NEUTRAL"

        signals.append(
            DCASignal(
                ticker=pos.ticker,
                name=pos.name,
                current_price=current,
                sma_30=round(sma_30, 2) if sma_30 is not None else None,
                sma_50=round(sma_50, 2) if sma_50 is not None else None,
                weight_pct=pos.weight_pct,
                pnl_pct=pos.pnl_pct,
                signal=signal,
            )
        )

    return signals


# ===================================================================
# Opportunity scanner
# ===================================================================

@dataclass
class Opportunity:
    """An investment opportunity based on IPS gaps + fundamentals."""

    bucket_name: str
    bucket_drift_pct: float     # negative = underweight
    ticker: str
    name: str
    signal: str                 # "ACCUMULATE" | "DEPLOY_CASH" | "TOP_UP"
    reason: str
    fundamental_score: int      # -1 if unknown
    valuation: str
    weight_pct: float
    dca_signal: str | None      # BUY_ZONE / NEUTRAL / EXTENDED / None


def scan_opportunities(
    bucket_drifts: list[dict],
    stock_scores: list[dict],
    dca_signals: list[dict],
    bucket_assignments: dict[str, str],
    cash_pct: float,
    min_cash_pct: float = 2.0,
) -> list[Opportunity]:
    """Identify deployment opportunities by cross-referencing IPS gaps with fundamentals.

    Logic:
    1. Find underweight buckets (negative drift).
    2. For each underweight bucket, find positions in that bucket.
    3. Rank by: valuation CHEAP > FAIR, fundamental_score descending,
       DCA BUY_ZONE preferred.
    4. If cash > min_cash_pct, additionally flag cash-deployment opportunities.

    Returns up to 5 opportunities, sorted by bucket drift magnitude.
    """
    # Build lookup maps
    score_map: dict[str, dict] = {s["ticker"]: s for s in stock_scores}
    dca_map: dict[str, str] = {s["ticker"]: s.get("signal", "NEUTRAL") for s in dca_signals}

    # Find underweight buckets
    underweight = [
        bd for bd in bucket_drifts
        if bd.get("drift_pct", 0) < -1.0  # at least 1pp underweight
    ]

    if not underweight:
        return []

    opportunities: list[Opportunity] = []

    for bd in sorted(underweight, key=lambda x: x.get("drift_pct", 0)):
        bucket_name = bd["bucket_name"]
        drift = bd["drift_pct"]

        # Find positions assigned to this bucket
        bucket_tickers = [
            t for t, b in bucket_assignments.items() if b == bucket_name
        ]

        # Score and rank candidates
        candidates = []
        for ticker in bucket_tickers:
            score = score_map.get(ticker, {})
            fs = score.get("fundamental_score", -1)
            val = score.get("valuation", "UNKNOWN")
            dca = dca_map.get(ticker)

            # Priority: CHEAP+BUY_ZONE > CHEAP > FAIR+BUY_ZONE > FAIR > rest
            rank = 0
            if val == "CHEAP":
                rank += 100
            elif val == "FAIR":
                rank += 50
            if dca == "BUY_ZONE":
                rank += 30
            elif dca == "EXTENDED":
                rank -= 20
            if fs > 0:
                rank += fs / 10  # 0–10 bonus

            candidates.append((ticker, score, dca, rank))

        candidates.sort(key=lambda x: -x[3])

        for ticker, score, dca, rank in candidates[:2]:
            name = score.get("name", ticker)
            fs = score.get("fundamental_score", -1)
            val = score.get("valuation", "UNKNOWN")
            wt = score.get("weight_pct", 0)

            # Determine signal type
            if dca == "BUY_ZONE" and val in ("CHEAP", "FAIR"):
                signal = "ACCUMULATE"
                reason = f"{bucket_name} underweight by {abs(drift):.1f}pp; price below SMA-30; {val.lower()} valuation"
            elif cash_pct > min_cash_pct + 3:
                signal = "DEPLOY_CASH"
                reason = f"{bucket_name} underweight by {abs(drift):.1f}pp; excess cash at {cash_pct:.1f}%"
            else:
                signal = "TOP_UP"
                reason = f"{bucket_name} underweight by {abs(drift):.1f}pp"

            opportunities.append(
                Opportunity(
                    bucket_name=bucket_name,
                    bucket_drift_pct=drift,
                    ticker=ticker,
                    name=name,
                    signal=signal,
                    reason=reason,
                    fundamental_score=fs,
                    valuation=val,
                    weight_pct=wt,
                    dca_signal=dca,
                )
            )

    return opportunities[:5]
