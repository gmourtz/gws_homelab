"""Tests for nodes.py — pipeline node implementations and message builder."""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import asdict

from nodes import (
    PipelineNodes,
    metrics_to_dict,
    dict_to_metrics,
    build_message,
)
from metrics import PortfolioMetrics, PositionMetric
from ips import Bucket, HardLimits, IPSConfig, RebalancingPolicy, Governance, Thresholds
from policy import PolicyEngine, Severity


# ---------------------------------------------------------------------------
# Serialisation roundtrip
# ---------------------------------------------------------------------------

def _make_pm() -> PortfolioMetrics:
    pos = PositionMetric(
        ticker="AAPL_US_EQ", name="Apple", market="US",
        quantity=10, avg_price=150, current_price=180,
        current_value=1800, weight_pct=18.0,
        pnl=300, pnl_pct=20.0, fx_impact=0,
    )
    return PortfolioMetrics(
        timestamp="2025-01-01 12:00",
        currency="GBP",
        total_value=10000, total_invested=9000,
        free_cash=500, cash_pct=5.0,
        overall_pnl=700, overall_pnl_pct=7.8,
        realized_pnl=0, num_positions=1,
        hhi=324, top1_weight=18.0, top1_ticker="AAPL_US_EQ",
        top3_weight=18.0, top5_weight=18.0,
        market_weights={"US": 18.0},
        positions=[pos],
        health_score=75,
        health_sub={"diversification": 20, "risk": 20, "cash": 20, "momentum": 15},
        winners=[pos], losers=[],
    )


class TestSerialisation:
    def test_roundtrip(self):
        pm = _make_pm()
        d = metrics_to_dict(pm)
        restored = dict_to_metrics(d)
        assert restored.total_value == pm.total_value
        assert restored.health_score == pm.health_score
        assert len(restored.positions) == len(pm.positions)
        assert restored.positions[0].ticker == pm.positions[0].ticker


# ---------------------------------------------------------------------------
# build_message
# ---------------------------------------------------------------------------

class TestBuildMessage:
    def test_healthy_message(self):
        pm_dict = metrics_to_dict(_make_pm())
        msg = build_message(pm_dict, [], None, False, {})
        assert "Portfolio Health" in msg
        assert "All clear" in msg

    def test_action_required_message(self):
        pm_dict = metrics_to_dict(_make_pm())
        alerts = [
            {"severity": "🔴 ACTION", "category": "concentration",
             "title": "Too concentrated", "detail": "Over limit", "rule": "IPS"},
        ]
        msg = build_message(pm_dict, alerts, None, True, {})
        assert "action(s) required" in msg
        assert "Too concentrated" in msg

    def test_warning_message(self):
        pm_dict = metrics_to_dict(_make_pm())
        alerts = [
            {"severity": "🟡 WARNING", "category": "cash",
             "title": "Cash low", "detail": "Below min", "rule": "IPS"},
        ]
        msg = build_message(pm_dict, alerts, None, False, {})
        assert "warning(s)" in msg

    def test_with_report(self):
        pm_dict = metrics_to_dict(_make_pm())
        report = {
            "executive_summary": ["Status ok", "Low risk", "Good opportunity"],
            "alert_explanations": [],
            "options": [{"name": "Hold", "recommendation": "Keep current allocation"}],
            "watchlist": [{"item": "AAPL earnings", "trigger": "Miss target"}],
            "caveats": ["AI analysis — not financial advice"],
        }
        msg = build_message(pm_dict, [], report, False, {})
        assert "Assessment" in msg
        assert "Status ok" in msg
        assert "not financial advice" in msg

    def test_with_timeseries(self):
        pm_dict = metrics_to_dict(_make_pm())
        state = {
            "timeseries_metrics": {
                "sharpe_ratio": 1.5,
                "max_drawdown_pct": -10.0,
                "annual_volatility_pct": 15.0,
            }
        }
        msg = build_message(pm_dict, [], None, False, state)
        assert "Sharpe" in msg
        assert "MaxDD" in msg


# ---------------------------------------------------------------------------
# PipelineNodes — individual node tests
# ---------------------------------------------------------------------------

class TestFetchNode:
    def test_successful_fetch(self):
        client = MagicMock()
        client.get_portfolio_snapshot.return_value = {
            "account": {}, "cash": {}, "positions": [{"x": 1}],
        }
        nodes = PipelineNodes(
            t212_client=client,
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=MagicMock(),
            notifier=MagicMock(),
            store=MagicMock(),
            ips=MagicMock(),
        )
        result = nodes.fetch({})
        assert result["snapshot"] is not None
        assert result["errors"] == []

    def test_fetch_returns_none(self):
        client = MagicMock()
        client.get_portfolio_snapshot.return_value = None
        nodes = PipelineNodes(
            t212_client=client,
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=MagicMock(),
            notifier=MagicMock(),
            store=MagicMock(),
            ips=MagicMock(),
        )
        result = nodes.fetch({})
        assert result["snapshot"] is None
        assert len(result["errors"]) >= 1

    def test_fetch_exception(self):
        client = MagicMock()
        client.get_portfolio_snapshot.side_effect = RuntimeError("network error")
        nodes = PipelineNodes(
            t212_client=client,
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=MagicMock(),
            notifier=MagicMock(),
            store=MagicMock(),
            ips=MagicMock(),
        )
        result = nodes.fetch({})
        assert result["snapshot"] is None
        assert "Fetch error" in result["errors"][0]


class TestValidateNode:
    def _make_nodes(self):
        return PipelineNodes(
            t212_client=MagicMock(),
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=MagicMock(),
            notifier=MagicMock(),
            store=MagicMock(),
            ips=MagicMock(),
        )

    def test_valid_snapshot(self):
        nodes = self._make_nodes()
        state = {
            "snapshot": {"account": {}, "cash": {}, "positions": [{"x": 1}]},
            "errors": [],
        }
        result = nodes.validate(state)
        assert result["errors"] == []

    def test_missing_keys(self):
        nodes = self._make_nodes()
        state = {"snapshot": {"account": {}}, "errors": []}
        result = nodes.validate(state)
        assert any("Missing" in e for e in result["errors"])

    def test_no_snapshot(self):
        nodes = self._make_nodes()
        result = nodes.validate({"snapshot": None, "errors": []})
        assert any("No snapshot" in e for e in result["errors"])

    def test_empty_positions(self):
        nodes = self._make_nodes()
        state = {
            "snapshot": {"account": {}, "cash": {}, "positions": []},
            "errors": [],
        }
        result = nodes.validate(state)
        assert any("Empty" in e for e in result["errors"])
