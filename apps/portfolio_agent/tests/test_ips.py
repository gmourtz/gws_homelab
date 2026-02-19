"""Tests for ips.py — IPS config loading and schema."""

import pytest
import tempfile
from pathlib import Path

from ips import Bucket, IPSConfig, HardLimits, load_ips


# ---------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------

class TestBucket:
    def test_drift(self):
        b = Bucket(name="US", target_pct=50.0, markets=["US"])
        assert abs(b.drift(55.0) - 5.0) < 0.01
        assert abs(b.drift(45.0) - (-5.0)) < 0.01

    def test_is_breached_within_bands(self):
        b = Bucket(name="US", target_pct=50.0, band_abs=5.0, band_rel=25.0, markets=["US"])
        breached, _ = b.is_breached(53.0)
        assert not breached

    def test_is_breached_absolute(self):
        b = Bucket(name="US", target_pct=50.0, band_abs=5.0, band_rel=25.0, markets=["US"])
        breached, _ = b.is_breached(60.0)
        assert breached

    def test_is_breached_relative(self):
        b = Bucket(name="Small", target_pct=5.0, band_abs=3.0, band_rel=25.0, markets=["JP"])
        # 7% → drift=2pp, relative = 2/5 = 40% > 25%
        breached, _ = b.is_breached(7.0)
        assert breached


# ---------------------------------------------------------------------------
# IPSConfig helpers
# ---------------------------------------------------------------------------

class TestIPSConfig:
    def _make_config(self) -> IPSConfig:
        return IPSConfig(
            version=1,
            base_currency="GBP",
            fx_treatment="convert_at_snapshot",
            buckets=[
                Bucket(name="US", target_pct=50.0, markets=["US"]),
                Bucket(name="Cash", target_pct=5.0, type="cash"),
            ],
            hard_limits=HardLimits(),
            rebalancing=__import__("ips").RebalancingPolicy(),
            governance=__import__("ips").Governance(),
            thresholds=__import__("ips").Thresholds(),
        )

    def test_equity_buckets(self):
        cfg = self._make_config()
        eq = cfg.equity_buckets()
        assert len(eq) == 1
        assert eq[0].name == "US"

    def test_cash_bucket(self):
        cfg = self._make_config()
        cb = cfg.cash_bucket()
        assert cb is not None
        assert cb.name == "Cash"

    def test_no_cash_bucket(self):
        cfg = IPSConfig(
            version=1,
            base_currency="GBP",
            fx_treatment="convert_at_snapshot",
            buckets=[Bucket(name="US", target_pct=100.0, markets=["US"])],
            hard_limits=HardLimits(),
            rebalancing=__import__("ips").RebalancingPolicy(),
            governance=__import__("ips").Governance(),
            thresholds=__import__("ips").Thresholds(),
        )
        assert cfg.cash_bucket() is None


# ---------------------------------------------------------------------------
# load_ips
# ---------------------------------------------------------------------------

class TestLoadIPS:
    def test_loads_built_in_ips(self):
        """The default ips.yml in src/ should load without error."""
        cfg = load_ips()
        assert cfg.version >= 1
        assert len(cfg.buckets) >= 2
        target_sum = sum(b.target_pct for b in cfg.buckets)
        assert abs(target_sum - 100.0) < 0.01

    def test_loads_from_custom_path(self, tmp_path):
        yaml_content = """
version: 2
base_currency: USD
allocation:
  buckets:
    - name: Test
      markets: [US]
      target_pct: 95.0
    - name: Cash
      type: cash
      target_pct: 5.0
hard_limits:
  max_single_name_pct: 25.0
"""
        p = tmp_path / "test_ips.yml"
        p.write_text(yaml_content)
        cfg = load_ips(p)
        assert cfg.version == 2
        assert cfg.base_currency == "USD"
        assert cfg.hard_limits.max_single_name_pct == 25.0
        assert len(cfg.buckets) == 2
