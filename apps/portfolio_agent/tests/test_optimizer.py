"""Tests for optimizer.py — rebalance candidate generation."""

import pytest
import numpy as np
import pandas as pd

from optimizer import (
    generate_rebalance_options,
    RebalanceCandidate,
    _hrp_allocate,
    _recursive_bisect,
    _cluster_var,
    _risk_reduction_rebalance,
    _HAS_SCIPY,
)


class TestGenerateRebalanceOptions:
    def _base_inputs(self) -> dict:
        return dict(
            current_weights={"A_US_EQ": 30.0, "Bl_EQ": 20.0, "C_AS_EQ": 10.0},
            bucket_assignments={"A_US_EQ": "US", "Bl_EQ": "UK", "C_AS_EQ": "EU"},
            bucket_targets={"US": 50.0, "UK": 20.0, "EU": 15.0, "Cash": 5.0},
            cash_pct=10.0,
            cash_target_pct=5.0,
            position_names={"A_US_EQ": "A Inc", "Bl_EQ": "B PLC", "C_AS_EQ": "C NV"},
            price_history=None,
        )

    def test_always_returns_at_least_two_candidates(self):
        options = generate_rebalance_options(**self._base_inputs())
        assert len(options) >= 2

    def test_first_candidate_is_policy_rebalance(self):
        options = generate_rebalance_options(**self._base_inputs())
        assert options[0].name == "Policy rebalance"

    def test_last_candidate_is_do_nothing(self):
        options = generate_rebalance_options(**self._base_inputs())
        assert "nothing" in options[-1].name.lower() or "monitor" in options[-1].name.lower()

    def test_do_nothing_has_zero_turnover(self):
        options = generate_rebalance_options(**self._base_inputs())
        do_nothing = options[-1]
        assert do_nothing.estimated_turnover_pct == 0.0
        assert len(do_nothing.trades) == 0

    def test_policy_rebalance_generates_trades_on_drift(self):
        inputs = self._base_inputs()
        # US is at 30% vs 50% target → significant drift
        options = generate_rebalance_options(**inputs)
        policy = options[0]
        assert len(policy.trades) >= 1

    def test_no_trades_when_within_threshold(self):
        """When all weights are close to targets, trades are minimal."""
        inputs = self._base_inputs()
        inputs["current_weights"] = {"A_US_EQ": 50.0, "Bl_EQ": 20.0, "C_AS_EQ": 15.0}
        options = generate_rebalance_options(**inputs)
        policy = options[0]
        # Should have few or no trades
        assert policy.estimated_turnover_pct < 2.0

    def test_all_candidates_have_required_fields(self):
        options = generate_rebalance_options(**self._base_inputs())
        for opt in options:
            assert isinstance(opt, RebalanceCandidate)
            assert opt.name
            assert opt.description
            assert isinstance(opt.pros, list)
            assert isinstance(opt.cons, list)
            assert opt.estimated_turnover_pct >= 0

    def test_breached_buckets_noted_in_do_nothing(self):
        inputs = self._base_inputs()
        inputs["breached_buckets"] = ["US", "EU"]
        options = generate_rebalance_options(**inputs)
        do_nothing = options[-1]
        assert "US" in do_nothing.policy_impact or "US" in str(do_nothing.cons)

    def test_with_price_history_generates_three_candidates(self):
        """When sufficient price history + scipy available, get 3 candidates."""
        if not _HAS_SCIPY:
            pytest.skip("scipy not installed")

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        price_history = pd.DataFrame({
            "A_US_EQ": np.cumsum(np.random.normal(0.1, 1, 60)) + 100,
            "Bl_EQ": np.cumsum(np.random.normal(0.05, 0.8, 60)) + 50,
            "C_AS_EQ": np.cumsum(np.random.normal(0.08, 1.2, 60)) + 200,
        }, index=dates)

        inputs = self._base_inputs()
        inputs["price_history"] = price_history
        options = generate_rebalance_options(**inputs)
        assert len(options) == 3
        assert options[1].name == "Risk-reduction rebalance"
        assert options[1].estimated_turnover_pct >= 0
        assert len(options[1].trades) >= 1

    def test_too_short_history_returns_two_candidates(self):
        """Price history with < 30 rows should not trigger HRP candidate."""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=15, freq="B")
        price_history = pd.DataFrame({
            "A_US_EQ": np.cumsum(np.random.normal(0, 1, 15)) + 100,
            "Bl_EQ": np.cumsum(np.random.normal(0, 1, 15)) + 50,
            "C_AS_EQ": np.cumsum(np.random.normal(0, 1, 15)) + 200,
        }, index=dates)

        inputs = self._base_inputs()
        inputs["price_history"] = price_history
        options = generate_rebalance_options(**inputs)
        assert len(options) == 2  # policy + do-nothing only


# ---------------------------------------------------------------------------
# HRP allocation internals
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not installed")
class TestHRPAllocate:
    def _make_returns(self, n_days=60, seed=42):
        np.random.seed(seed)
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        return pd.DataFrame({
            "A": np.random.normal(0.001, 0.02, n_days),
            "B": np.random.normal(0.0005, 0.015, n_days),
            "C": np.random.normal(0.0008, 0.025, n_days),
            "D": np.random.normal(0.0003, 0.01, n_days),
        }, index=dates)

    def test_weights_sum_to_one(self):
        returns = self._make_returns()
        weights = _hrp_allocate(returns)
        assert weights is not None
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_all_tickers_present(self):
        returns = self._make_returns()
        weights = _hrp_allocate(returns)
        assert weights is not None
        assert set(weights.keys()) == {"A", "B", "C", "D"}

    def test_all_weights_positive(self):
        returns = self._make_returns()
        weights = _hrp_allocate(returns)
        assert weights is not None
        assert all(w > 0 for w in weights.values())

    def test_low_vol_gets_higher_weight(self):
        """The lowest-volatility asset (D) should get more weight than the highest (C)."""
        returns = self._make_returns()
        weights = _hrp_allocate(returns)
        assert weights is not None
        assert weights["D"] > weights["C"]

    def test_returns_none_with_fewer_than_3_tickers(self):
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        returns = pd.DataFrame({
            "A": np.random.normal(0, 0.02, 30),
            "B": np.random.normal(0, 0.02, 30),
        }, index=dates)
        assert _hrp_allocate(returns) is None

    def test_deterministic_with_same_seed(self):
        w1 = _hrp_allocate(self._make_returns(seed=99))
        w2 = _hrp_allocate(self._make_returns(seed=99))
        assert w1 is not None and w2 is not None
        for k in w1:
            assert abs(w1[k] - w2[k]) < 1e-10


@pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not installed")
class TestRecursiveBisect:
    def test_weights_sum_to_one(self):
        np.random.seed(42)
        returns = pd.DataFrame({
            "X": np.random.normal(0, 0.02, 50),
            "Y": np.random.normal(0, 0.02, 50),
            "Z": np.random.normal(0, 0.02, 50),
        })
        cov = returns.cov()
        weights = _recursive_bisect(cov, ["X", "Y", "Z"])
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_single_ticker(self):
        cov = pd.DataFrame({"A": [0.04]}, index=["A"])
        weights = _recursive_bisect(cov, ["A"])
        assert abs(weights["A"] - 1.0) < 1e-6

    def test_two_tickers_inverse_variance(self):
        """Two assets: higher-variance one gets lower weight."""
        cov = pd.DataFrame(
            [[0.01, 0.002], [0.002, 0.04]],
            index=["LOW", "HIGH"], columns=["LOW", "HIGH"],
        )
        weights = _recursive_bisect(cov, ["LOW", "HIGH"])
        assert weights["LOW"] > weights["HIGH"]


@pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not installed")
class TestClusterVar:
    def test_single_asset(self):
        cov = pd.DataFrame({"A": [0.04]}, index=["A"])
        # Single asset: inv-variance portfolio = 100% in A, var = 0.04
        var = _cluster_var(cov, ["A"])
        assert abs(var - 0.04) < 1e-6

    def test_two_uncorrelated(self):
        cov = pd.DataFrame(
            [[0.04, 0.0], [0.0, 0.04]],
            index=["A", "B"], columns=["A", "B"],
        )
        var = _cluster_var(cov, ["A", "B"])
        # Equal variance, zero corr → inv-var gives 50/50 → var = 0.25 * 0.04 + 0.25 * 0.04 = 0.02
        assert abs(var - 0.02) < 1e-6


# ---------------------------------------------------------------------------
# Risk-reduction rebalance (integration)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not installed")
class TestRiskReductionRebalance:
    def test_generates_trades(self):
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        price_history = pd.DataFrame({
            "A_US_EQ": np.cumsum(np.random.normal(0.1, 1, 60)) + 100,
            "B_US_EQ": np.cumsum(np.random.normal(0.05, 0.8, 60)) + 50,
            "C_US_EQ": np.cumsum(np.random.normal(0.08, 1.2, 60)) + 200,
        }, index=dates)
        current_weights = {"A_US_EQ": 60.0, "B_US_EQ": 25.0, "C_US_EQ": 15.0}
        names = {"A_US_EQ": "A Inc", "B_US_EQ": "B Corp", "C_US_EQ": "C Ltd"}

        result = _risk_reduction_rebalance(current_weights, price_history, names)
        assert result is not None
        assert result.name == "Risk-reduction rebalance"
        assert len(result.trades) >= 1
        assert result.estimated_turnover_pct > 0
        # Heavily concentrated A should be trimmed
        a_trade = next((t for t in result.trades if t.ticker == "A_US_EQ"), None)
        if a_trade:
            assert a_trade.target_weight_pct < 60.0

    def test_returns_none_with_too_few_held_tickers(self):
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        price_history = pd.DataFrame({
            "A_US_EQ": np.cumsum(np.random.normal(0, 1, 60)) + 100,
            "B_US_EQ": np.cumsum(np.random.normal(0, 1, 60)) + 50,
            "C_US_EQ": np.cumsum(np.random.normal(0, 1, 60)) + 200,
        }, index=dates)
        # Only 2 tickers held (need >= 3)
        current_weights = {"A_US_EQ": 70.0, "B_US_EQ": 30.0}
        result = _risk_reduction_rebalance(current_weights, price_history, {})
        assert result is None

    def test_returns_none_with_short_returns(self):
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        price_history = pd.DataFrame({
            "A": range(10), "B": range(10), "C": range(10),
        }, index=dates)
        current_weights = {"A": 40.0, "B": 30.0, "C": 30.0}
        result = _risk_reduction_rebalance(current_weights, price_history, {})
        assert result is None
