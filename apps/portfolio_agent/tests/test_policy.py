"""Tests for policy.py — IPS rule engine."""

import pytest

from ips import Bucket, HardLimits, IPSConfig, RebalancingPolicy, Governance, Thresholds
from metrics import PortfolioMetrics, PositionMetric, TimeSeriesMetrics
from policy import Alert, BucketDrift, PolicyEngine, Severity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _default_ips(**overrides) -> IPSConfig:
    """Build a minimal IPS config with defaults."""
    kwargs = dict(
        version=1,
        base_currency="GBP",
        fx_treatment="convert_at_snapshot",
        buckets=[
            Bucket(name="US Equity", target_pct=50.0, markets=["US"]),
            Bucket(name="UK Equity", target_pct=20.0, markets=["UK"]),
            Bucket(name="Other", target_pct=25.0, markets=["*"]),
            Bucket(name="Cash", target_pct=5.0, type="cash"),
        ],
        hard_limits=HardLimits(),
        rebalancing=RebalancingPolicy(),
        governance=Governance(),
        thresholds=Thresholds(),
    )
    kwargs.update(overrides)
    return IPSConfig(**kwargs)


def _make_position(
    ticker: str = "AAPL_US_EQ",
    name: str = "Apple",
    market: str = "US",
    weight_pct: float = 10.0,
    pnl_pct: float = 5.0,
    current_price: float = 180.0,
    current_value: float = 1800.0,
) -> PositionMetric:
    return PositionMetric(
        ticker=ticker,
        name=name,
        market=market,
        quantity=10,
        avg_price=150.0,
        current_price=current_price,
        current_value=current_value,
        weight_pct=weight_pct,
        pnl=0,
        pnl_pct=pnl_pct,
        fx_impact=0,
    )


def _make_metrics(**overrides) -> PortfolioMetrics:
    """Build a PortfolioMetrics with sensible defaults."""
    positions = overrides.pop("positions", [
        _make_position("A_US_EQ", "A", "US", 15.0, 5.0),
        _make_position("B_US_EQ", "B", "US", 10.0, 3.0),
        _make_position("Cl_EQ", "C", "UK", 10.0, -2.0),
    ])
    kwargs = dict(
        timestamp="2025-01-01 12:00",
        currency="GBP",
        total_value=10000,
        total_invested=9000,
        free_cash=500,
        cash_pct=5.0,
        overall_pnl=700,
        overall_pnl_pct=7.8,
        realized_pnl=0,
        num_positions=len(positions),
        hhi=800,
        top1_weight=positions[0].weight_pct if positions else 0,
        top1_ticker=positions[0].ticker if positions else "",
        top3_weight=sum(p.weight_pct for p in positions[:3]),
        top5_weight=sum(p.weight_pct for p in positions[:5]),
        market_weights=_aggregate_market_weights(positions),
        positions=positions,
        health_score=75,
        health_sub={"diversification": 20, "risk": 20, "cash": 20, "momentum": 15},
        winners=[p for p in positions if p.pnl_pct > 0],
        losers=[p for p in positions if p.pnl_pct < 0],
    )
    kwargs.update(overrides)
    return PortfolioMetrics(**kwargs)


def _aggregate_market_weights(positions: list[PositionMetric]) -> dict[str, float]:
    mw: dict[str, float] = {}
    for p in positions:
        mw[p.market] = mw.get(p.market, 0) + p.weight_pct
    return mw


# ---------------------------------------------------------------------------
# Bucket.is_breached
# ---------------------------------------------------------------------------

class TestBucketBreach:
    def test_within_bands(self):
        b = Bucket(name="US", target_pct=50.0, band_abs=5.0, band_rel=25.0, markets=["US"])
        breached, drift = b.is_breached(52.0)
        assert not breached
        assert abs(drift - 2.0) < 0.01

    def test_absolute_breach(self):
        b = Bucket(name="US", target_pct=50.0, band_abs=5.0, band_rel=25.0, markets=["US"])
        breached, drift = b.is_breached(56.0)
        assert breached
        assert abs(drift - 6.0) < 0.01

    def test_relative_breach_small_bucket(self):
        """Small bucket (5% target) → 25% relative = 1.25pp absolute."""
        b = Bucket(name="Cash", target_pct=5.0, band_abs=3.0, band_rel=25.0, type="cash")
        # Actual 7% → drift 2pp. Relative: 2/5 = 40% > 25% → breach
        breached, drift = b.is_breached(7.0)
        assert breached

    def test_zero_target_no_relative_breach(self):
        b = Bucket(name="Empty", target_pct=0.0, band_abs=5.0, band_rel=25.0, markets=[])
        breached, _ = b.is_breached(3.0)
        assert not breached  # Only absolute breach possible, 3 < 5


# ---------------------------------------------------------------------------
# Concentration checks
# ---------------------------------------------------------------------------

class TestConcentration:
    def test_single_name_over_limit(self):
        ips = _default_ips(hard_limits=HardLimits(max_single_name_pct=15.0))
        engine = PolicyEngine(ips)
        positions = [
            _make_position("BIG_US_EQ", "BigCo", "US", 20.0),
        ]
        m = _make_metrics(positions=positions, top1_weight=20.0)
        alerts, _, action = engine.evaluate(m)
        action_alerts = [a for a in alerts if a.severity == Severity.ACTION and a.category == "concentration"]
        assert len(action_alerts) >= 1
        assert action

    def test_top3_over_limit(self):
        ips = _default_ips(hard_limits=HardLimits(max_top3_pct=40.0))
        engine = PolicyEngine(ips)
        positions = [
            _make_position("A_US_EQ", "A", "US", 18.0),
            _make_position("B_US_EQ", "B", "US", 15.0),
            _make_position("C_US_EQ", "C", "US", 12.0),
        ]
        m = _make_metrics(
            positions=positions,
            top3_weight=45.0,
            top1_weight=18.0,
        )
        alerts, _, _ = engine.evaluate(m)
        warning_alerts = [a for a in alerts if a.category == "concentration" and a.severity == Severity.WARNING]
        assert len(warning_alerts) >= 1

    def test_no_concentration_alert_when_within_limits(self):
        ips = _default_ips()
        engine = PolicyEngine(ips)
        m = _make_metrics(top1_weight=10.0, top3_weight=25.0)
        alerts, _, _ = engine.evaluate(m)
        concentration_alerts = [a for a in alerts if a.category == "concentration"]
        assert len(concentration_alerts) == 0


# ---------------------------------------------------------------------------
# Cash checks
# ---------------------------------------------------------------------------

class TestCash:
    def test_cash_below_minimum(self):
        ips = _default_ips(hard_limits=HardLimits(min_cash_pct=2.0))
        engine = PolicyEngine(ips)
        m = _make_metrics(cash_pct=1.0)
        alerts, _, _ = engine.evaluate(m)
        cash_warnings = [a for a in alerts if a.category == "cash"]
        assert any(a.severity == Severity.WARNING for a in cash_warnings)

    def test_cash_above_maximum(self):
        ips = _default_ips(hard_limits=HardLimits(max_cash_pct=25.0))
        engine = PolicyEngine(ips)
        m = _make_metrics(cash_pct=30.0)
        alerts, _, _ = engine.evaluate(m)
        cash_alerts = [a for a in alerts if a.category == "cash"]
        assert any(a.severity == Severity.INFO for a in cash_alerts)

    def test_cash_within_range(self):
        ips = _default_ips()
        engine = PolicyEngine(ips)
        m = _make_metrics(cash_pct=5.0)
        alerts, _, _ = engine.evaluate(m)
        cash_alerts = [a for a in alerts if a.category == "cash"]
        assert len(cash_alerts) == 0


# ---------------------------------------------------------------------------
# P/L thresholds
# ---------------------------------------------------------------------------

class TestPnLThresholds:
    def test_profit_taking_candidate(self):
        ips = _default_ips(thresholds=Thresholds(profit_taking_pct=100.0))
        engine = PolicyEngine(ips)
        big_winner = _make_position("WIN_US_EQ", "Winner", "US", 10.0, 150.0)
        m = _make_metrics(
            positions=[big_winner],
            winners=[big_winner],
            losers=[],
            top1_weight=10.0,
        )
        alerts, _, _ = engine.evaluate(m)
        profit_alerts = [a for a in alerts if a.category == "profit_taking"]
        assert len(profit_alerts) == 1

    def test_deep_loss_warning(self):
        ips = _default_ips(thresholds=Thresholds(deep_loss_pct=-30.0, critical_loss_pct=-50.0))
        engine = PolicyEngine(ips)
        loser = _make_position("LOSE_US_EQ", "Loser", "US", 5.0, -35.0)
        m = _make_metrics(
            positions=[loser],
            winners=[],
            losers=[loser],
            top1_weight=5.0,
        )
        alerts, _, _ = engine.evaluate(m)
        loss_alerts = [a for a in alerts if a.category == "loss_review"]
        assert len(loss_alerts) == 1
        assert loss_alerts[0].severity == Severity.WARNING

    def test_critical_loss_action(self):
        ips = _default_ips(thresholds=Thresholds(deep_loss_pct=-30.0, critical_loss_pct=-50.0))
        engine = PolicyEngine(ips)
        loser = _make_position("CRIT_US_EQ", "Critical", "US", 5.0, -55.0)
        m = _make_metrics(
            positions=[loser],
            winners=[],
            losers=[loser],
            top1_weight=5.0,
        )
        alerts, _, action = engine.evaluate(m)
        loss_alerts = [a for a in alerts if a.category == "loss_review"]
        assert len(loss_alerts) == 1
        assert loss_alerts[0].severity == Severity.ACTION
        assert action


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

class TestHealth:
    def test_critical_health(self):
        ips = _default_ips(thresholds=Thresholds(health_critical=40, health_warning=60))
        engine = PolicyEngine(ips)
        m = _make_metrics(health_score=30)
        alerts, _, action = engine.evaluate(m)
        health_alerts = [a for a in alerts if a.category == "health"]
        assert any(a.severity == Severity.ACTION for a in health_alerts)
        assert action

    def test_warning_health(self):
        ips = _default_ips(thresholds=Thresholds(health_critical=40, health_warning=60))
        engine = PolicyEngine(ips)
        m = _make_metrics(health_score=50)
        alerts, _, _ = engine.evaluate(m)
        health_alerts = [a for a in alerts if a.category == "health"]
        assert any(a.severity == Severity.WARNING for a in health_alerts)

    def test_healthy_no_alert(self):
        ips = _default_ips(thresholds=Thresholds(health_critical=40, health_warning=60))
        engine = PolicyEngine(ips)
        m = _make_metrics(health_score=80)
        alerts, _, _ = engine.evaluate(m)
        health_alerts = [a for a in alerts if a.category == "health"]
        assert len(health_alerts) == 0


# ---------------------------------------------------------------------------
# Drawdown (time-series)
# ---------------------------------------------------------------------------

class TestDrawdown:
    def test_drawdown_exceeds_tolerance(self):
        ips = _default_ips(hard_limits=HardLimits(max_drawdown_pct=20.0))
        engine = PolicyEngine(ips)
        m = _make_metrics()
        ts = TimeSeriesMetrics(
            history_days=60,
            annual_return_pct=-5.0,
            annual_volatility_pct=20.0,
            sharpe_ratio=-0.45,
            sortino_ratio=None,
            max_drawdown_pct=-25.0,
            current_drawdown_pct=-18.0,
            rolling_30d_vol_pct=None,
            calmar_ratio=None,
            correlation_clusters=None,
        )
        alerts, _, action = engine.evaluate(m, ts_metrics=ts)
        dd_alerts = [a for a in alerts if a.category == "drawdown"]
        assert len(dd_alerts) >= 1
        assert dd_alerts[0].severity == Severity.ACTION
        assert action

    def test_no_drawdown_alert_with_short_history(self):
        ips = _default_ips()
        engine = PolicyEngine(ips)
        m = _make_metrics()
        ts = TimeSeriesMetrics(
            history_days=10,
            annual_return_pct=None,
            annual_volatility_pct=None,
            sharpe_ratio=None,
            sortino_ratio=None,
            max_drawdown_pct=-25.0,
            current_drawdown_pct=-18.0,
            rolling_30d_vol_pct=None,
            calmar_ratio=None,
            correlation_clusters=None,
        )
        alerts, _, _ = engine.evaluate(m, ts_metrics=ts)
        dd_alerts = [a for a in alerts if a.category == "drawdown"]
        assert len(dd_alerts) == 0


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_zero_price_flagged(self):
        ips = _default_ips()
        engine = PolicyEngine(ips)
        positions = [_make_position("BAD_US_EQ", "Bad", "US", 10.0, 0.0, current_price=0.0)]
        m = _make_metrics(positions=positions, top1_weight=10.0)
        alerts, _, action = engine.evaluate(m)
        data_alerts = [a for a in alerts if a.category == "data" and "price" in a.title.lower()]
        assert len(data_alerts) >= 1
        assert action

    def test_limited_history_info(self):
        ips = _default_ips()
        engine = PolicyEngine(ips)
        m = _make_metrics()
        # No time-series → limited history alert
        alerts, _, _ = engine.evaluate(m, ts_metrics=None)
        data_alerts = [a for a in alerts if a.category == "data" and "history" in a.title.lower()]
        assert len(data_alerts) >= 1
        assert data_alerts[0].severity == Severity.INFO


# ---------------------------------------------------------------------------
# Alert sorting
# ---------------------------------------------------------------------------

class TestAlertSorting:
    def test_actions_sorted_first(self):
        ips = _default_ips(
            hard_limits=HardLimits(max_single_name_pct=10.0),
            thresholds=Thresholds(health_critical=99),
        )
        engine = PolicyEngine(ips)
        positions = [_make_position("BIG_US_EQ", "Big", "US", 15.0)]
        m = _make_metrics(positions=positions, top1_weight=15.0, health_score=50)
        alerts, _, _ = engine.evaluate(m)
        # Should have multiple alerts; ACTIONs come first
        if len(alerts) >= 2:
            severities = [a.severity for a in alerts]
            action_indices = [i for i, s in enumerate(severities) if s == Severity.ACTION]
            non_action_indices = [i for i, s in enumerate(severities) if s != Severity.ACTION]
            if action_indices and non_action_indices:
                assert max(action_indices) < min(non_action_indices)
