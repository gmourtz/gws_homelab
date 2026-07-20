"""Tests for the ingestion script (health_to_sqlite.py / ingest.py)."""

import sqlite3
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ingest import (
    parse_dt,
    fmt_dt,
    fmt_date,
    safe_float,
    sleep_stage_label,
    _merge_minutes,
    _group_sleep_sessions,
    build_sleep,
    _dedup_workouts,
    build_daily_summary,
    MANAGED_TABLES,
    MANUAL_TABLES,
)
from datetime import datetime, timedelta


class TestHelpers:
    def test_parse_dt_with_tz(self):
        dt = parse_dt("2026-07-20 13:30:00 +0100")
        assert dt is not None
        assert dt.hour == 13

    def test_parse_dt_without_tz(self):
        dt = parse_dt("2026-07-20 13:30:00")
        assert dt is not None

    def test_parse_dt_none(self):
        assert parse_dt(None) is None
        assert parse_dt("") is None

    def test_fmt_dt(self):
        dt = datetime(2026, 7, 20, 13, 30, 0)
        assert fmt_dt(dt) == "2026-07-20 13:30:00"
        assert fmt_dt(None) == ""

    def test_fmt_date(self):
        dt = datetime(2026, 7, 20, 13, 30, 0)
        assert fmt_date(dt) == "2026-07-20"

    def test_safe_float(self):
        assert safe_float("3.14159", 2) == 3.14
        assert safe_float("invalid") is None
        assert safe_float(None) is None
        assert safe_float("") is None

    def test_sleep_stage_label(self):
        assert sleep_stage_label("HKCategoryValueSleepAnalysisAsleepDeep") == "Deep"
        assert sleep_stage_label("HKCategoryValueSleepAnalysisAsleepCore") == "Core"
        assert sleep_stage_label("HKCategoryValueSleepAnalysisAsleepUnspecified") == "Core"
        assert sleep_stage_label("HKCategoryValueSleepAnalysisAwake") == "Awake"
        assert sleep_stage_label("HKCategoryValueSleepAnalysisInBed") == "In Bed"


class TestSleepAggregation:
    def test_merge_minutes_no_overlap(self):
        now = datetime(2026, 7, 20, 0, 0)
        intervals = [
            (now, now + timedelta(minutes=30)),
            (now + timedelta(minutes=60), now + timedelta(minutes=90)),
        ]
        assert _merge_minutes(intervals) == 60.0

    def test_merge_minutes_overlap(self):
        now = datetime(2026, 7, 20, 0, 0)
        intervals = [
            (now, now + timedelta(minutes=60)),
            (now + timedelta(minutes=30), now + timedelta(minutes=90)),
        ]
        assert _merge_minutes(intervals) == 90.0

    def test_group_sleep_sessions_splits_on_gap(self):
        base = datetime(2026, 7, 20, 22, 0)
        sleep_data = [
            # Night session
            (base, base + timedelta(hours=7), "Core"),
            # Nap 4 hours later (> 2h gap)
            (base + timedelta(hours=11), base + timedelta(hours=11, minutes=30), "Core"),
        ]
        sessions = _group_sleep_sessions(sleep_data)
        assert len(sessions) == 2

    def test_build_sleep_keeps_longest(self):
        # Two sessions ending on the same wake date — longest wins
        base = datetime(2026, 7, 20, 22, 0)
        sleep_data = [
            # Main night: 7 hours
            (base, base + timedelta(hours=7), "Core"),
            # Nap same wake-day: 30 min (gap > 2h so separate session)
            (base + timedelta(hours=11), base + timedelta(hours=11, minutes=30), "Core"),
        ]
        rows = build_sleep(sleep_data)
        # Both end on 2026-07-21 — only one row kept (the 7-hour one)
        dates = [r[0] for r in rows]
        assert dates.count("2026-07-21") == 1
        # The kept row should have ~420 min
        main = [r for r in rows if r[0] == "2026-07-21"][0]
        assert main[3] >= 400  # time_in_bed_min


class TestWorkoutDedup:
    def test_keeps_unique_workouts(self):
        now = datetime(2026, 7, 20, 10, 0)
        w1 = {"type": "Running", "start": now, "end": now + timedelta(minutes=45),
               "duration": 45.0, "distance": 8.0, "dist_unit": "km", "calories": 400}
        w2 = {"type": "Cycling", "start": now + timedelta(hours=3),
               "end": now + timedelta(hours=3, minutes=60),
               "duration": 60.0, "distance": 20.0, "dist_unit": "km", "calories": 500}
        result = _dedup_workouts([(w1, [130, 140, 150]), (w2, [120, 125])])
        assert len(result) == 2

    def test_removes_duplicate_same_start(self):
        now = datetime(2026, 7, 20, 10, 0)
        w1 = {"type": "Running", "start": now, "end": now + timedelta(minutes=45),
               "duration": 45.0, "distance": 8.0, "dist_unit": "km", "calories": 400}
        # Same workout logged again (duplicate)
        w2 = {"type": "Running", "start": now, "end": now + timedelta(minutes=45),
               "duration": 45.0, "distance": 8.0, "dist_unit": "km", "calories": 400}
        # w1 has more HR readings → kept
        result = _dedup_workouts([(w1, [130, 140, 150, 145]), (w2, [130, 140])])
        assert len(result) == 1


class TestManualTablesGuard:
    def test_managed_and_manual_dont_overlap(self):
        """The ingestion script must never touch manual tables."""
        assert set(MANAGED_TABLES).isdisjoint(set(MANUAL_TABLES))

    def test_all_managed_tables_exist(self):
        """Every managed table name must be valid SQL."""
        schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
        schema = open(schema_path).read()
        for table in MANAGED_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in schema

    def test_all_manual_tables_exist(self):
        schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
        schema = open(schema_path).read()
        for table in MANUAL_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
