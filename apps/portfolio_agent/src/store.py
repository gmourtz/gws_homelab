"""Time-series persistence layer.

Stores daily portfolio snapshots as JSON-lines for computing returns,
drawdown, volatility, and correlation.  Designed for a Docker volume
mount at /data (falls back to ./data for local dev).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_DOCKER_DATA = Path("/data")
_LOCAL_DATA = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DailySnapshot:
    """Minimal daily record for time-series analysis."""

    date: str                        # YYYY-MM-DD
    total_value: float
    invested: float
    cash: float
    positions: dict[str, float]      # ticker → current_value
    weights: dict[str, float]        # ticker → weight_pct
    prices: dict[str, float]         # ticker → current_price


@dataclass
class PersistentState:
    """Agent state persisted between runs."""

    last_run: str | None = None
    last_rebalance_date: str | None = None
    last_review_date: str | None = None
    run_count: int = 0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class SnapshotStore:
    """Append-only JSONL store for daily portfolio snapshots."""

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir:
            self.data_dir = Path(data_dir)
        elif _DOCKER_DATA.exists():
            self.data_dir = _DOCKER_DATA
        else:
            self.data_dir = _LOCAL_DATA

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots_file = self.data_dir / "snapshots.jsonl"
        self._state_file = self.data_dir / "state.json"
        self._cached_raw: list[dict] | None = None
        log.info("Store initialised at %s", self.data_dir)

    def invalidate_cache(self) -> None:
        """Clear the in-memory snapshot cache (call after writes)."""
        self._cached_raw = None

    # ── Snapshots ──────────────────────────────────────────────────

    def append_snapshot(self, snap: DailySnapshot) -> None:
        """Append a daily snapshot.  Skips if today already recorded."""
        existing = self._load_raw()
        if any(s.get("date") == snap.date for s in existing):
            log.info("Snapshot for %s already exists — skipping", snap.date)
            return

        with open(self._snapshots_file, "a") as f:
            f.write(json.dumps(asdict(snap)) + "\n")
        self.invalidate_cache()
        log.info(
            "Stored snapshot for %s (value=%.2f)", snap.date, snap.total_value
        )

    def snapshot_count(self) -> int:
        return len(self._load_raw())

    # ── Returns series ─────────────────────────────────────────────

    def get_returns(self) -> pd.Series | None:
        """Daily portfolio returns.  None if < 2 snapshots."""
        snaps = self._load_raw()
        if len(snaps) < 2:
            return None

        df = pd.DataFrame(snaps)[["date", "total_value"]]
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates("date").set_index("date")
        return df["total_value"].pct_change().dropna()

    def get_values(self) -> pd.Series | None:
        """Daily total portfolio value.  None if < 2 snapshots."""
        snaps = self._load_raw()
        if len(snaps) < 2:
            return None

        df = pd.DataFrame(snaps)[["date", "total_value"]]
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates("date").set_index("date")
        return df["total_value"]

    def get_price_history(self) -> pd.DataFrame | None:
        """Ticker prices over time (columns = tickers, index = dates).

        Returns None if < 5 snapshots.
        """
        snaps = self._load_raw()
        if len(snaps) < 5:
            return None

        records = []
        for s in snaps:
            row = {"date": s["date"]}
            row.update(s.get("prices", {}))
            records.append(row)

        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates("date").set_index("date")
        return df

    def get_weight_history(self) -> pd.DataFrame | None:
        """Ticker weights over time.  None if < 5 snapshots."""
        snaps = self._load_raw()
        if len(snaps) < 5:
            return None

        records = []
        for s in snaps:
            row = {"date": s["date"]}
            row.update(s.get("weights", {}))
            records.append(row)

        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates("date").set_index("date")
        return df.fillna(0)

    # ── Persistent state ───────────────────────────────────────────

    def load_state(self) -> PersistentState:
        if not self._state_file.exists():
            return PersistentState()
        try:
            raw = json.loads(self._state_file.read_text())
            valid = {f for f in PersistentState.__dataclass_fields__}
            return PersistentState(**{k: v for k, v in raw.items() if k in valid})
        except Exception as e:
            log.warning("Failed to load state: %s", e)
            return PersistentState()

    def save_state(self, state: PersistentState) -> None:
        self._state_file.write_text(json.dumps(asdict(state), indent=2) + "\n")

    def mark_run(self, state: PersistentState) -> PersistentState:
        """Return updated state after a successful run."""
        return PersistentState(
            last_run=date.today().isoformat(),
            last_rebalance_date=state.last_rebalance_date,
            last_review_date=state.last_review_date,
            run_count=state.run_count + 1,
        )

    # ── Internal ───────────────────────────────────────────────────

    def _load_raw(self) -> list[dict]:
        if self._cached_raw is not None:
            return self._cached_raw
        if not self._snapshots_file.exists():
            return []
        lines = self._snapshots_file.read_text().strip().split("\n")
        out: list[dict] = []
        for line in lines:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Skipping malformed snapshot line")
        self._cached_raw = out
        return out
