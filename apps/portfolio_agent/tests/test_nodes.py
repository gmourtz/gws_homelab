"""Tests for nodes.py — pipeline node implementations and message builder."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import asdict
from datetime import date

from nodes import (
    PipelineNodes,
    metrics_to_dict,
    dict_to_metrics,
    build_message,
)
from metrics import PortfolioMetrics, PositionMetric, TimeSeriesMetrics
from ips import Bucket, HardLimits, IPSConfig, RebalancingPolicy, Governance, Thresholds
from policy import PolicyEngine, Alert, BucketDrift, Severity
from store import SnapshotStore, PersistentState


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


# ---------------------------------------------------------------------------
# Helpers shared by node tests below
# ---------------------------------------------------------------------------

def _make_ips() -> IPSConfig:
    return IPSConfig(
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


def _make_snapshot() -> dict:
    """Minimal T212 snapshot with 2 US + 1 UK position."""
    return {
        "account": {"currency": "GBP"},
        "cash": {
            "invested": 9000, "free": 500, "ppl": 700,
            "realizedPpl": 0, "totalValue": 10000,
        },
        "positions": [
            {
                "instrument": {"ticker": "AAPL_US_EQ", "name": "Apple"},
                "quantity": 10, "averagePricePaid": 150, "currentPrice": 180,
                "walletImpact": {"currentValue": 4000, "unrealizedProfitLoss": 300, "fxImpact": 5},
            },
            {
                "instrument": {"ticker": "MSFT_US_EQ", "name": "Microsoft"},
                "quantity": 5, "averagePricePaid": 300, "currentPrice": 350,
                "walletImpact": {"currentValue": 3500, "unrealizedProfitLoss": 250, "fxImpact": 3},
            },
            {
                "instrument": {"ticker": "CCLl_EQ", "name": "Carnival"},
                "quantity": 100, "averagePricePaid": 8, "currentPrice": 10,
                "walletImpact": {"currentValue": 2000, "unrealizedProfitLoss": 200, "fxImpact": 0},
            },
        ],
    }


def _make_pipeline_nodes(
    store: SnapshotStore | None = None,
    ips: IPSConfig | None = None,
) -> PipelineNodes:
    if ips is None:
        ips = _make_ips()
    return PipelineNodes(
        t212_client=MagicMock(),
        finnhub_client=None,
        analyzer=MagicMock(),
        policy_engine=PolicyEngine(ips),
        notifier=MagicMock(),
        store=store or MagicMock(),
        ips=ips,
    )


# ---------------------------------------------------------------------------
# Node 3: update_store
# ---------------------------------------------------------------------------

class TestUpdateStoreNode:
    def test_persists_snapshot(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.update_store({"snapshot": _make_snapshot()})

        assert result["history_days"] == 1
        assert store.snapshot_count() == 1
        assert "persistent_state" in result

    def test_handles_no_snapshot(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.update_store({"snapshot": None})
        assert result["history_days"] == 0
        assert store.snapshot_count() == 0

    def test_extracts_position_values(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        nodes.update_store({"snapshot": _make_snapshot()})

        # Verify the stored data contains the right tickers
        raw = store._load_raw()
        assert len(raw) == 1
        stored = raw[0]
        assert "AAPL_US_EQ" in stored["positions"]
        assert "MSFT_US_EQ" in stored["positions"]
        assert "CCLl_EQ" in stored["positions"]
        assert stored["total_value"] == 10000

    def test_no_duplicate_on_same_day(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        snap = _make_snapshot()
        nodes.update_store({"snapshot": snap})
        nodes.update_store({"snapshot": snap})
        assert store.snapshot_count() == 1


# ---------------------------------------------------------------------------
# Node 4: compute
# ---------------------------------------------------------------------------

class TestComputeNode:
    def test_produces_metrics(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        state = {"snapshot": _make_snapshot()}

        result = nodes.compute(state)
        pm = result["portfolio_metrics"]

        assert pm is not None
        assert pm["total_value"] == 10000
        assert pm["num_positions"] == 3
        assert pm["health_score"] > 0
        assert "US" in pm["market_weights"]

    def test_timeseries_none_with_no_history(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.compute({"snapshot": _make_snapshot()})
        assert result["timeseries_metrics"] is None

    def test_handles_no_snapshot(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.compute({"snapshot": None})
        assert result == {}

    def test_positions_sorted_by_weight(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.compute({"snapshot": _make_snapshot()})
        positions = result["portfolio_metrics"]["positions"]
        weights = [p["weight_pct"] for p in positions]
        assert weights == sorted(weights, reverse=True)


# ---------------------------------------------------------------------------
# Node 5: optimize
# ---------------------------------------------------------------------------

class TestOptimizeNode:
    def test_generates_candidates(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)

        # First compute metrics
        pm_dict = nodes.compute({"snapshot": _make_snapshot()})["portfolio_metrics"]
        result = nodes.optimize({"portfolio_metrics": pm_dict})

        opts = result["rebalance_options"]
        assert len(opts) >= 2
        assert opts[0]["name"] == "Policy rebalance"
        assert "nothing" in opts[-1]["name"].lower() or "monitor" in opts[-1]["name"].lower()

    def test_returns_empty_on_no_metrics(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.optimize({"portfolio_metrics": None})
        assert result["rebalance_options"] == []

    def test_bucket_assignments_align_with_ips(self, tmp_path):
        """Positions should be assigned to correct IPS buckets."""
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        pm_dict = nodes.compute({"snapshot": _make_snapshot()})["portfolio_metrics"]
        result = nodes.optimize({"portfolio_metrics": pm_dict})

        # Policy rebalance should reference bucket targets
        policy_rb = result["rebalance_options"][0]
        if policy_rb["trades"]:
            # All trades should have a rationale mentioning a target %
            for t in policy_rb["trades"]:
                assert "target" in t["rationale"].lower()


# ---------------------------------------------------------------------------
# Node 6: evaluate_policy
# ---------------------------------------------------------------------------

class TestEvaluatePolicyNode:
    def test_returns_alerts_and_drifts(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        pm_dict = nodes.compute({"snapshot": _make_snapshot()})["portfolio_metrics"]

        result = nodes.evaluate_policy({
            "portfolio_metrics": pm_dict,
            "timeseries_metrics": None,
            "persistent_state": {},
        })

        assert "alerts" in result
        assert "bucket_drifts" in result
        assert "action_required" in result
        assert isinstance(result["alerts"], list)
        assert isinstance(result["bucket_drifts"], list)

    def test_detects_concentration_breach(self, tmp_path):
        """A portfolio with one 40% position should trigger concentration alert."""
        store = SnapshotStore(tmp_path)
        ips = _make_ips()
        # Lower limit to trigger
        ips_low = IPSConfig(
            version=1, base_currency="GBP", fx_treatment="convert_at_snapshot",
            buckets=ips.buckets,
            hard_limits=HardLimits(max_single_name_pct=15.0),
            rebalancing=RebalancingPolicy(),
            governance=Governance(),
            thresholds=Thresholds(),
        )
        nodes = PipelineNodes(
            t212_client=MagicMock(),
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=PolicyEngine(ips_low),
            notifier=MagicMock(),
            store=store,
            ips=ips_low,
        )

        # Concentrated snapshot — AAPL = 40%
        snap = {
            "account": {"currency": "GBP"},
            "cash": {"invested": 9000, "free": 500, "ppl": 500, "realizedPpl": 0, "totalValue": 10000},
            "positions": [
                {
                    "instrument": {"ticker": "AAPL_US_EQ", "name": "Apple"},
                    "quantity": 10, "averagePricePaid": 150, "currentPrice": 400,
                    "walletImpact": {"currentValue": 7000, "unrealizedProfitLoss": 2500, "fxImpact": 0},
                },
                {
                    "instrument": {"ticker": "CCLl_EQ", "name": "Carnival"},
                    "quantity": 100, "averagePricePaid": 8, "currentPrice": 10,
                    "walletImpact": {"currentValue": 2500, "unrealizedProfitLoss": 200, "fxImpact": 0},
                },
            ],
        }
        pm_dict = nodes.compute({"snapshot": snap})["portfolio_metrics"]
        result = nodes.evaluate_policy({
            "portfolio_metrics": pm_dict,
            "timeseries_metrics": None,
            "persistent_state": {},
        })

        assert result["action_required"]
        conc_alerts = [
            a for a in result["alerts"]
            if a["category"] == "concentration" and "ACTION" in a["severity"]
        ]
        assert len(conc_alerts) >= 1

    def test_returns_empty_on_no_metrics(self, tmp_path):
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        result = nodes.evaluate_policy({"portfolio_metrics": None})
        assert result["alerts"] == []
        assert not result["action_required"]

    def test_bucket_drifts_are_serialisable(self, tmp_path):
        """All bucket drifts should be dicts with expected keys."""
        store = SnapshotStore(tmp_path)
        nodes = _make_pipeline_nodes(store=store)
        pm_dict = nodes.compute({"snapshot": _make_snapshot()})["portfolio_metrics"]
        result = nodes.evaluate_policy({
            "portfolio_metrics": pm_dict,
            "timeseries_metrics": None,
            "persistent_state": {},
        })
        for bd in result["bucket_drifts"]:
            assert "bucket_name" in bd
            assert "target_pct" in bd
            assert "actual_pct" in bd
            assert "breached" in bd


# ---------------------------------------------------------------------------
# Node 9: notify
# ---------------------------------------------------------------------------

class TestNotifyNode:
    def test_sends_message_on_valid_state(self, tmp_path):
        store = SnapshotStore(tmp_path)
        notifier = MagicMock()
        ips = _make_ips()
        nodes = PipelineNodes(
            t212_client=MagicMock(),
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=PolicyEngine(ips),
            notifier=notifier,
            store=store,
            ips=ips,
        )

        pm_dict = metrics_to_dict(_make_pm())
        state = {
            "snapshot": _make_snapshot(),
            "errors": [],
            "portfolio_metrics": pm_dict,
            "alerts": [],
            "action_required": False,
            "report": None,
        }

        result = nodes.notify(state)
        assert result["sent"]
        assert result["message"]
        notifier.send.assert_called_once()
        # Message should contain health info
        sent_msg = notifier.send.call_args[0][0]
        assert "Portfolio Health" in sent_msg

    def test_sends_error_when_no_snapshot(self, tmp_path):
        store = SnapshotStore(tmp_path)
        notifier = MagicMock()
        nodes = PipelineNodes(
            t212_client=MagicMock(),
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=MagicMock(),
            notifier=notifier,
            store=store,
            ips=_make_ips(),
        )

        result = nodes.notify({
            "snapshot": None,
            "errors": ["Fetch error: timeout"],
        })
        assert result["sent"]
        notifier.send.assert_called_once()
        sent_msg = notifier.send.call_args[0][0]
        assert "could not fetch" in sent_msg.lower()

    def test_updates_persistent_state(self, tmp_path):
        store = SnapshotStore(tmp_path)
        notifier = MagicMock()
        nodes = PipelineNodes(
            t212_client=MagicMock(),
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=PolicyEngine(_make_ips()),
            notifier=notifier,
            store=store,
            ips=_make_ips(),
        )

        pm_dict = metrics_to_dict(_make_pm())
        nodes.notify({
            "snapshot": _make_snapshot(),
            "errors": [],
            "portfolio_metrics": pm_dict,
            "alerts": [],
            "action_required": False,
            "report": None,
        })

        # State should have been saved with incremented run_count
        loaded = store.load_state()
        assert loaded.run_count == 1
        assert loaded.last_run is not None

    def test_includes_alerts_in_message(self, tmp_path):
        store = SnapshotStore(tmp_path)
        notifier = MagicMock()
        nodes = PipelineNodes(
            t212_client=MagicMock(),
            finnhub_client=None,
            analyzer=MagicMock(),
            policy_engine=PolicyEngine(_make_ips()),
            notifier=notifier,
            store=store,
            ips=_make_ips(),
        )

        pm_dict = metrics_to_dict(_make_pm())
        nodes.notify({
            "snapshot": _make_snapshot(),
            "errors": [],
            "portfolio_metrics": pm_dict,
            "alerts": [
                {"severity": "🔴 ACTION", "category": "concentration",
                 "title": "Single name breach", "detail": "Over 20%",
                 "rule": "IPS §3", "tickers": ["AAPL_US_EQ"]},
            ],
            "action_required": True,
            "report": None,
        })

        sent_msg = notifier.send.call_args[0][0]
        assert "action(s) required" in sent_msg
        assert "Single name breach" in sent_msg
