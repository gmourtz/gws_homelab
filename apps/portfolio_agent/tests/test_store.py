"""Tests for store.py — snapshot persistence and time-series retrieval."""

import json
import pytest
from pathlib import Path

from store import DailySnapshot, PersistentState, SnapshotStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_snapshot(day: int, total_value: float = 10000.0) -> DailySnapshot:
    return DailySnapshot(
        date=f"2025-01-{day:02d}",
        total_value=total_value,
        invested=9000.0,
        cash=1000.0,
        positions={"AAPL_US_EQ": total_value * 0.6, "GOOGL_US_EQ": total_value * 0.4},
        weights={"AAPL_US_EQ": 60.0, "GOOGL_US_EQ": 40.0},
        prices={"AAPL_US_EQ": 180.0, "GOOGL_US_EQ": 150.0},
    )


# ---------------------------------------------------------------------------
# SnapshotStore
# ---------------------------------------------------------------------------

class TestSnapshotStore:
    def test_append_and_count(self, tmp_path):
        store = SnapshotStore(tmp_path)
        assert store.snapshot_count() == 0

        store.append_snapshot(_make_snapshot(1))
        assert store.snapshot_count() == 1

        store.append_snapshot(_make_snapshot(2))
        assert store.snapshot_count() == 2

    def test_skip_duplicate_date(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.append_snapshot(_make_snapshot(1))
        store.append_snapshot(_make_snapshot(1))  # same date
        assert store.snapshot_count() == 1

    def test_cache_invalidation_on_append(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.append_snapshot(_make_snapshot(1))

        # Force cache population
        assert store.snapshot_count() == 1

        # Append should invalidate cache
        store.append_snapshot(_make_snapshot(2))
        assert store.snapshot_count() == 2

    def test_get_returns_none_with_few_snapshots(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.append_snapshot(_make_snapshot(1))
        assert store.get_returns() is None

    def test_get_returns_with_enough_data(self, tmp_path):
        store = SnapshotStore(tmp_path)
        for d in range(1, 6):
            store.append_snapshot(_make_snapshot(d, total_value=10000 + d * 100))
        returns = store.get_returns()
        assert returns is not None
        assert len(returns) >= 1

    def test_get_values(self, tmp_path):
        store = SnapshotStore(tmp_path)
        for d in range(1, 6):
            store.append_snapshot(_make_snapshot(d, total_value=10000 + d * 100))
        values = store.get_values()
        assert values is not None
        assert len(values) >= 2

    def test_get_price_history_needs_5_snapshots(self, tmp_path):
        store = SnapshotStore(tmp_path)
        for d in range(1, 4):
            store.append_snapshot(_make_snapshot(d))
        assert store.get_price_history() is None

        for d in range(4, 7):
            store.append_snapshot(_make_snapshot(d))
        ph = store.get_price_history()
        assert ph is not None
        assert "AAPL_US_EQ" in ph.columns

    def test_malformed_lines_skipped(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.append_snapshot(_make_snapshot(1))

        # Manually inject a bad line
        snapshots_file = tmp_path / "snapshots.jsonl"
        with open(snapshots_file, "a") as f:
            f.write("this is not json\n")

        store.invalidate_cache()
        assert store.snapshot_count() == 1  # bad line skipped


# ---------------------------------------------------------------------------
# PersistentState
# ---------------------------------------------------------------------------

class TestPersistentState:
    def test_save_and_load(self, tmp_path):
        store = SnapshotStore(tmp_path)
        state = PersistentState(
            last_run="2025-01-01",
            run_count=5,
        )
        store.save_state(state)
        loaded = store.load_state()
        assert loaded.last_run == "2025-01-01"
        assert loaded.run_count == 5

    def test_load_returns_default_when_missing(self, tmp_path):
        store = SnapshotStore(tmp_path)
        state = store.load_state()
        assert state.run_count == 0
        assert state.last_run is None

    def test_mark_run(self, tmp_path):
        store = SnapshotStore(tmp_path)
        state = PersistentState(run_count=3)
        updated = store.mark_run(state)
        assert updated.run_count == 4
        assert updated.last_run is not None
