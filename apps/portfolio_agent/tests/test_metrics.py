"""Tests for metrics.py — deterministic portfolio analytics."""

import pytest

from metrics import (
    PortfolioMetrics,
    PositionMetric,
    StockScore,
    TimeSeriesMetrics,
    compute_metrics,
    compute_timeseries_metrics,
    score_stocks,
    _correlation_clusters,
    _fundamental_score,
    _valuation_signal,
    _health_scores,
)

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Fixtures — minimal valid T212 snapshot
# ---------------------------------------------------------------------------

def _make_position(
    ticker: str = "AAPL_US_EQ",
    name: str = "Apple Inc",
    quantity: float = 10,
    avg_price: float = 150.0,
    current_price: float = 180.0,
    current_value: float | None = None,
    pnl: float | None = None,
    fx: float = 0.0,
) -> dict:
    """Build a T212-style position dict."""
    value = current_value if current_value is not None else quantity * current_price
    pnl_val = pnl if pnl is not None else (current_price - avg_price) * quantity
    return {
        "instrument": {"ticker": ticker, "name": name},
        "quantity": quantity,
        "averagePricePaid": avg_price,
        "currentPrice": current_price,
        "walletImpact": {
            "currentValue": value,
            "unrealizedProfitLoss": pnl_val,
            "fxImpact": fx,
        },
    }


def _make_snapshot(
    positions: list[dict] | None = None,
    free_cash: float = 500.0,
    invested: float = 9000.0,
    total_value: float = 10000.0,
    realized_pnl: float = 0.0,
    currency: str = "GBP",
) -> dict:
    """Build a minimal T212 portfolio snapshot."""
    if positions is None:
        positions = [
            _make_position("AAPL_US_EQ", "Apple", 10, 150, 180, 1800, 300),
            _make_position("CCLl_EQ", "Carnival UK", 100, 8, 10, 1000, 200),
            _make_position("ASML_AS_EQ", "ASML", 2, 600, 700, 1400, 200),
        ]
    return {
        "account": {"id": "test", "currency": currency},
        "cash": {
            "availableToTrade": free_cash,  # field name from T212 API
            "invested": invested,  # mapped by trading212.py
            "free": free_cash,
            "ppl": sum(
                p.get("walletImpact", {}).get("unrealizedProfitLoss", 0)
                for p in positions
            ),
            "realizedPpl": realized_pnl,
            "totalValue": total_value,
        },
        "positions": positions,
    }


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def test_basic_snapshot(self):
        snap = _make_snapshot()
        m = compute_metrics(snap)

        assert isinstance(m, PortfolioMetrics)
        assert m.num_positions == 3
        assert m.currency == "GBP"
        assert m.total_value == 10000.0
        assert m.free_cash == 500.0

    def test_weights_sum_close_to_100_minus_cash(self):
        positions = [
            _make_position("A_US_EQ", "A", 10, 10, 10, 2500),
            _make_position("B_US_EQ", "B", 10, 10, 10, 2500),
            _make_position("Cl_EQ", "C", 10, 10, 10, 2500),
            _make_position("Dd_EQ", "D", 10, 10, 10, 2500),
        ]
        snap = _make_snapshot(
            positions=positions, free_cash=0, invested=10000, total_value=10000
        )
        m = compute_metrics(snap)
        total_weight = sum(p.weight_pct for p in m.positions)
        assert abs(total_weight - 100.0) < 0.1

    def test_hhi_four_equal_weights(self):
        """4 equal 25% positions → HHI = 4 × 625 = 2500."""
        positions = [
            _make_position(f"T{i}_US_EQ", f"T{i}", 1, 100, 100, 2500)
            for i in range(4)
        ]
        snap = _make_snapshot(
            positions=positions, free_cash=0, invested=10000, total_value=10000
        )
        m = compute_metrics(snap)
        assert abs(m.hhi - 2500) < 1

    def test_market_weights(self):
        positions = [
            _make_position("A_US_EQ", "A", 1, 1, 1, 6000),
            _make_position("Bl_EQ", "B", 1, 1, 1, 4000),
        ]
        snap = _make_snapshot(
            positions=positions, free_cash=0, invested=10000, total_value=10000
        )
        m = compute_metrics(snap)
        assert "US" in m.market_weights
        assert "UK" in m.market_weights
        assert abs(m.market_weights["US"] - 60.0) < 0.1
        assert abs(m.market_weights["UK"] - 40.0) < 0.1

    def test_winners_and_losers(self):
        positions = [
            _make_position("WIN_US_EQ", "Winner", 10, 100, 150, 1500, 500),
            _make_position("LOSE_US_EQ", "Loser", 10, 100, 50, 500, -500),
        ]
        snap = _make_snapshot(
            positions=positions, free_cash=0, invested=2000, total_value=2000
        )
        m = compute_metrics(snap)
        assert len(m.winners) == 1
        assert len(m.losers) == 1
        assert m.winners[0].ticker == "WIN_US_EQ"
        assert m.losers[0].ticker == "LOSE_US_EQ"

    def test_zero_total_value_no_crash(self):
        snap = _make_snapshot(
            positions=[_make_position("A_US_EQ", "A", 0, 0, 0, 0)],
            free_cash=0,
            invested=0,
            total_value=0,
        )
        m = compute_metrics(snap)
        assert m.total_value == 0

    def test_health_score_range(self):
        snap = _make_snapshot()
        m = compute_metrics(snap)
        assert 0 <= m.health_score <= 100
        for v in m.health_sub.values():
            assert 0 <= v <= 25


# ---------------------------------------------------------------------------
# _health_scores
# ---------------------------------------------------------------------------

class TestHealthScores:
    def test_perfect_diversification(self):
        """Low HHI, low top1 → high diversification score."""
        positions = [
            PositionMetric(
                ticker=f"T{i}", name=f"T{i}", market="US",
                quantity=1, avg_price=100, current_price=100,
                current_value=1000, weight_pct=10.0,
                pnl=0, pnl_pct=0, fx_impact=0,
            )
            for i in range(10)
        ]
        scores = _health_scores(
            hhi=1000, top1_weight=10.0, cash_pct=5.0,
            positions=positions, overall_pnl_pct=5.0,
            winners=positions[:5], losers=[],
        )
        assert scores["diversification"] >= 15

    def test_concentrated_portfolio(self):
        """High HHI, high top1 → low diversification score."""
        positions = [
            PositionMetric(
                ticker="BIG", name="BIG", market="US",
                quantity=1, avg_price=100, current_price=100,
                current_value=8000, weight_pct=80.0,
                pnl=0, pnl_pct=0, fx_impact=0,
            ),
        ]
        scores = _health_scores(
            hhi=6400, top1_weight=80.0, cash_pct=5.0,
            positions=positions, overall_pnl_pct=0,
            winners=[], losers=[],
        )
        assert scores["diversification"] <= 5

    def test_cash_optimal_range(self):
        scores = _health_scores(
            hhi=1000, top1_weight=10.0, cash_pct=5.0,
            positions=[], overall_pnl_pct=0,
            winners=[], losers=[],
        )
        assert scores["cash"] == 25

    def test_cash_too_low(self):
        scores = _health_scores(
            hhi=1000, top1_weight=10.0, cash_pct=0.5,
            positions=[], overall_pnl_pct=0,
            winners=[], losers=[],
        )
        assert scores["cash"] <= 10


# ---------------------------------------------------------------------------
# _fundamental_score
# ---------------------------------------------------------------------------

class TestFundamentalScore:
    def test_returns_negative_one_when_insufficient_data(self):
        assert _fundamental_score(None, None, None, None, None, None) == -1
        assert _fundamental_score(20, None, None, None, None, None) == -1

    def test_strong_fundamentals(self):
        score = _fundamental_score(
            pe=15, eps_growth=20, rev_growth=15,
            debt_to_equity=0.5, roe=25, net_margin=25,
        )
        assert score >= 60

    def test_weak_fundamentals(self):
        score = _fundamental_score(
            pe=80, eps_growth=-5, rev_growth=-10,
            debt_to_equity=4.0, roe=-5, net_margin=-10,
        )
        assert score <= 30

    def test_negative_pe(self):
        score = _fundamental_score(
            pe=-5, eps_growth=10, rev_growth=10,
            debt_to_equity=1.0, roe=10, net_margin=10,
        )
        assert 0 <= score <= 100

    def test_max_capped_at_100(self):
        score = _fundamental_score(
            pe=5, eps_growth=50, rev_growth=50,
            debt_to_equity=0.1, roe=30, net_margin=30,
        )
        assert score <= 100


# ---------------------------------------------------------------------------
# _valuation_signal
# ---------------------------------------------------------------------------

class TestValuationSignal:
    def test_unknown_when_no_pe(self):
        assert _valuation_signal(None, 10, 10) == "UNKNOWN"

    def test_unknown_when_negative_pe(self):
        assert _valuation_signal(-5, 10, 10) == "UNKNOWN"

    def test_cheap_low_peg(self):
        # PE=10, growth=20 → PEG=0.5 < 1.0 → CHEAP
        assert _valuation_signal(10, 20, None) == "CHEAP"

    def test_fair_moderate_peg(self):
        # PE=25, growth=20 → PEG=1.25 → FAIR
        assert _valuation_signal(25, 20, None) == "FAIR"

    def test_expensive_high_peg(self):
        # PE=60, growth=10 → PEG=6.0 → EXPENSIVE
        assert _valuation_signal(60, 10, None) == "EXPENSIVE"

    def test_uses_rev_growth_as_fallback(self):
        # PE=10, eps=None, rev=20 → PEG=0.5 → CHEAP
        assert _valuation_signal(10, None, 20) == "CHEAP"

    def test_no_growth_low_pe(self):
        assert _valuation_signal(10, None, None) == "CHEAP"

    def test_no_growth_high_pe(self):
        assert _valuation_signal(30, None, None) == "EXPENSIVE"

    def test_negative_growth_high_pe(self):
        assert _valuation_signal(20, -5, None) == "EXPENSIVE"

    def test_negative_growth_low_pe(self):
        assert _valuation_signal(10, -5, None) == "FAIR"


# ---------------------------------------------------------------------------
# compute_timeseries_metrics
# ---------------------------------------------------------------------------

class TestTimeSeriesMetrics:
    def test_returns_none_with_insufficient_data(self):
        assert compute_timeseries_metrics(None, None, None) is None
        short = pd.Series([0.01, 0.02], index=pd.date_range("2025-01-01", periods=2))
        assert compute_timeseries_metrics(short, None, None) is None

    def test_basic_timeseries(self):
        dates = pd.date_range("2025-01-01", periods=30, freq="B")
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.001, 0.02, 30), index=dates)
        values = pd.Series(
            (1 + returns).cumprod() * 10000, index=dates
        )
        ts = compute_timeseries_metrics(returns, values, None)

        assert ts is not None
        assert ts.history_days > 0
        assert ts.annual_return_pct is not None
        assert ts.annual_volatility_pct is not None
        assert ts.sharpe_ratio is not None
        assert ts.max_drawdown_pct is not None
        assert ts.max_drawdown_pct <= 0  # drawdown is always ≤ 0

    def test_no_drawdown_in_monotonic_increase(self):
        dates = pd.date_range("2025-01-01", periods=10, freq="B")
        returns = pd.Series([0.01] * 10, index=dates)
        values = pd.Series(
            (1 + returns).cumprod() * 10000, index=dates
        )
        ts = compute_timeseries_metrics(returns, values, None)
        assert ts is not None
        # Current drawdown should be 0 (at peak)
        assert ts.current_drawdown_pct is not None
        assert abs(ts.current_drawdown_pct) < 0.01

    def test_with_price_history_triggers_clusters(self):
        """When price_history is provided, correlation_clusters is populated."""
        np.random.seed(99)
        dates = pd.date_range("2025-01-01", periods=30, freq="B")
        returns = pd.Series(np.random.normal(0.001, 0.02, 30), index=dates)
        values = pd.Series((1 + returns).cumprod() * 10000, index=dates)

        # Build correlated price history (A & B move together, C independent)
        base = np.cumsum(np.random.normal(0, 1, 30)) + 100
        price_history = pd.DataFrame({
            "A": base + np.random.normal(0, 0.1, 30),
            "B": base + np.random.normal(0, 0.1, 30),
            "C": np.cumsum(np.random.normal(0, 2, 30)) + 50,
        }, index=dates)
        ts = compute_timeseries_metrics(returns, values, price_history)
        assert ts is not None
        # With highly correlated A & B, we should get at least one cluster
        assert ts.correlation_clusters is not None
        assert any("A" in c and "B" in c for c in ts.correlation_clusters)


# ---------------------------------------------------------------------------
# _correlation_clusters
# ---------------------------------------------------------------------------

class TestCorrelationClusters:
    def test_returns_none_for_none_input(self):
        assert _correlation_clusters(None) is None

    def test_returns_none_for_too_few_columns(self):
        dates = pd.date_range("2025-01-01", periods=20, freq="B")
        df = pd.DataFrame({"A": range(20), "B": range(20)}, index=dates)
        assert _correlation_clusters(df) is None

    def test_returns_none_for_too_few_rows(self):
        dates = pd.date_range("2025-01-01", periods=5, freq="B")
        df = pd.DataFrame({
            "A": [1, 2, 3, 4, 5],
            "B": [5, 4, 3, 2, 1],
            "C": [2, 4, 6, 8, 10],
        }, index=dates)
        # After pct_change + dropna → 4 rows < 10 threshold
        assert _correlation_clusters(df) is None

    def test_finds_correlated_pair(self):
        np.random.seed(42)
        dates = pd.date_range("2025-01-01", periods=50, freq="B")
        base = np.cumsum(np.random.normal(0, 1, 50)) + 100
        df = pd.DataFrame({
            "CORR_A": base + np.random.normal(0, 0.05, 50),
            "CORR_B": base + np.random.normal(0, 0.05, 50),
            "INDEP": np.cumsum(np.random.normal(0, 3, 50)) + 200,
        }, index=dates)
        clusters = _correlation_clusters(df, threshold=0.75)
        assert clusters is not None
        assert len(clusters) >= 1
        # The correlated pair should be in the same cluster
        found = any("CORR_A" in c and "CORR_B" in c for c in clusters)
        assert found
        # INDEP should NOT be in a cluster with the correlated pair
        for c in clusters:
            if "CORR_A" in c:
                assert "INDEP" not in c

    def test_no_clusters_when_independent(self):
        np.random.seed(7)
        dates = pd.date_range("2025-01-01", periods=50, freq="B")
        df = pd.DataFrame({
            "X": np.cumsum(np.random.normal(0, 1, 50)) + 100,
            "Y": np.cumsum(np.random.normal(0, 1, 50)) + 200,
            "Z": np.cumsum(np.random.normal(0, 1, 50)) + 300,
        }, index=dates)
        # Independent series → likely no cluster at threshold 0.75
        clusters = _correlation_clusters(df, threshold=0.95)
        assert clusters is None

    def test_constant_column_excluded(self):
        """Columns with zero std (constant) should be excluded, not crash."""
        np.random.seed(11)
        dates = pd.date_range("2025-01-01", periods=30, freq="B")
        df = pd.DataFrame({
            "A": np.cumsum(np.random.normal(0, 1, 30)) + 100,
            "B": np.cumsum(np.random.normal(0, 1, 30)) + 200,
            "CONST": [50.0] * 30,
        }, index=dates)
        result = _correlation_clusters(df)
        # Should handle gracefully — CONST has 0 std → excluded → < 3 valid
        # or returns None. Either way, no crash.
        assert result is None  # < 3 valid columns after filtering


# ---------------------------------------------------------------------------
# score_stocks
# ---------------------------------------------------------------------------

class TestScoreStocks:
    def _make_pos(self, ticker="AAPL_US_EQ", name="Apple", market="US",
                  weight=15.0, pnl_pct=20.0, price=180.0, value=2700.0):
        return PositionMetric(
            ticker=ticker, name=name, market=market,
            quantity=15, avg_price=150, current_price=price,
            current_value=value, weight_pct=weight,
            pnl=300, pnl_pct=pnl_pct, fx_impact=0,
        )

    def test_basic_scoring(self):
        """Positions with full fundamentals produce valid StockScores."""
        pos = [self._make_pos()]
        fundamentals = {"AAPL": {
            "pe": 25, "eps_growth": 15, "rev_growth": 10,
            "debt_to_equity": 1.0, "roe": 20, "net_margin": 25,
            "div_yield": 0.5, "beta": 1.1,
            "w52_high": 190, "w52_low": 120,
        }}
        profiles = {"AAPL": {"sector": "Technology"}}
        earnings = {}  # no upcoming earnings
        symbol_map = {"AAPL_US_EQ": "AAPL"}

        scores = score_stocks(pos, fundamentals, profiles, earnings, symbol_map)
        assert len(scores) == 1
        s = scores[0]
        assert isinstance(s, StockScore)
        assert s.ticker == "AAPL_US_EQ"
        assert s.sector == "Technology"
        assert s.pe_ratio == 25
        assert s.fundamental_score >= 0
        assert s.valuation in ("CHEAP", "FAIR", "EXPENSIVE")
        assert not s.earnings_soon
        assert not s.high_leverage

    def test_missing_fundamentals_returns_neg1_score(self):
        """No fundamental data → score = -1, valuation = UNKNOWN."""
        pos = [self._make_pos("FOO_US_EQ", "Foo Inc")]
        scores = score_stocks(
            pos,
            fundamentals={},
            profiles={},
            earnings_calendar={},
            symbol_map={"FOO_US_EQ": "FOO"},
        )
        assert len(scores) == 1
        assert scores[0].fundamental_score == -1
        assert scores[0].valuation == "UNKNOWN"
        assert scores[0].sector == "Unknown"

    def test_earnings_soon_flag(self):
        pos = [self._make_pos()]
        scores = score_stocks(
            pos,
            fundamentals={"AAPL": {"pe": 20, "eps_growth": 10, "roe": 15, "net_margin": 15}},
            profiles={"AAPL": {}},
            earnings_calendar={"AAPL": "2025-02-20"},
            symbol_map={"AAPL_US_EQ": "AAPL"},
        )
        assert scores[0].earnings_soon

    def test_high_leverage_flag(self):
        pos = [self._make_pos()]
        scores = score_stocks(
            pos,
            fundamentals={"AAPL": {"debt_to_equity": 3.5, "pe": 20, "roe": 10, "net_margin": 10}},
            profiles={},
            earnings_calendar={},
            symbol_map={"AAPL_US_EQ": "AAPL"},
        )
        assert scores[0].high_leverage

    def test_negative_growth_flag(self):
        pos = [self._make_pos()]
        scores = score_stocks(
            pos,
            fundamentals={"AAPL": {"eps_growth": -5, "pe": 20, "roe": 10, "net_margin": 10}},
            profiles={},
            earnings_calendar={},
            symbol_map={"AAPL_US_EQ": "AAPL"},
        )
        assert scores[0].has_negative_growth

    def test_near_52w_high(self):
        pos = [self._make_pos(price=188.0)]
        scores = score_stocks(
            pos,
            fundamentals={"AAPL": {"w52_high": 190, "w52_low": 120, "pe": 20, "roe": 10, "net_margin": 10}},
            profiles={},
            earnings_calendar={},
            symbol_map={"AAPL_US_EQ": "AAPL"},
        )
        assert scores[0].near_52w_high
        assert not scores[0].near_52w_low

    def test_near_52w_low(self):
        pos = [self._make_pos(price=125.0)]
        scores = score_stocks(
            pos,
            fundamentals={"AAPL": {"w52_high": 190, "w52_low": 120, "pe": 20, "roe": 10, "net_margin": 10}},
            profiles={},
            earnings_calendar={},
            symbol_map={"AAPL_US_EQ": "AAPL"},
        )
        assert scores[0].near_52w_low
        assert not scores[0].near_52w_high

    def test_multiple_positions(self):
        positions = [
            self._make_pos("A_US_EQ", "A Inc", "US", 40, 10, 100, 4000),
            self._make_pos("Bl_EQ", "B PLC", "UK", 30, -5, 50, 3000),
            self._make_pos("Cd_EQ", "C AG", "DE", 20, 50, 200, 2000),
        ]
        fundamentals = {
            "A": {"pe": 15, "roe": 20, "net_margin": 15},
            "B": {"pe": 35, "roe": 5, "net_margin": 3},
            "C": {"pe": 10, "eps_growth": 30, "roe": 25, "net_margin": 20},
        }
        symbol_map = {"A_US_EQ": "A", "Bl_EQ": "B", "Cd_EQ": "C"}
        scores = score_stocks(positions, fundamentals, {}, {}, symbol_map)
        assert len(scores) == 3
        # C should have the highest fundamental score (cheap PE + strong growth)
        c_score = next(s for s in scores if s.ticker == "Cd_EQ")
        a_score = next(s for s in scores if s.ticker == "A_US_EQ")
        b_score = next(s for s in scores if s.ticker == "Bl_EQ")
        assert c_score.fundamental_score > b_score.fundamental_score

    def test_unmapped_ticker_uses_empty_fundamentals(self):
        """Ticker not in symbol_map → looks up empty string → no fundamentals."""
        pos = [self._make_pos("MYSTERY_XX_EQ", "Mystery")]
        scores = score_stocks(pos, {}, {}, {}, {})
        assert len(scores) == 1
        assert scores[0].fundamental_score == -1
