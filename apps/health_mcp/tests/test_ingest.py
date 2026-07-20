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
    _vdot_paces,
    _min_dec_to_str,
    _estimate_lthr,
    build_training_zones,
    parse_export,
    init_db,
    write_to_db,
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


class TestVDOTPaces:
    def test_exact_table_row(self):
        # VDOT 50 maps directly to the table row (no interpolation).
        e_slow, e_fast, m, t, i, r_400 = _vdot_paces(50)
        assert e_slow == 5.30
        assert e_fast == 4.80
        assert m == 4.08
        assert t == 3.83
        assert i == 3.55
        assert r_400 == 82

    def test_interpolates_between_rows(self):
        # VDOT 52.5 is halfway between the 50 and 55 rows.
        paces = _vdot_paces(52.5)
        row50 = (5.30, 4.80, 4.08, 3.83, 3.55, 82)
        row55 = (4.88, 4.43, 3.72, 3.48, 3.22, 74)
        for got, lo, hi in zip(paces, row50, row55):
            assert abs(got - (lo + hi) / 2) < 0.01

    def test_faster_vdot_gives_faster_paces(self):
        slow = _vdot_paces(40)
        fast = _vdot_paces(65)
        # Lower min/km value = faster. Every zone should be faster at higher VDOT.
        for f, s in zip(fast[:5], slow[:5]):
            assert f < s

    def test_clamps_below_table(self):
        assert _vdot_paces(20) == _vdot_paces(30)

    def test_clamps_above_table(self):
        assert _vdot_paces(90) == _vdot_paces(70)


class TestMinDecToStr:
    def test_whole_minutes(self):
        assert _min_dec_to_str(4.0) == "4:00"

    def test_fractional_minutes(self):
        assert _min_dec_to_str(4.5) == "4:30"

    def test_rounds_seconds(self):
        # 3.83 min → 3 min + 0.83*60 = 49.8s → 50s
        assert _min_dec_to_str(3.83) == "3:50"


class TestEstimateLTHR:
    def _hr_series(self, start, minutes, bpm):
        """One HR reading per minute at a constant bpm."""
        return [(start + timedelta(minutes=m), bpm) for m in range(minutes)]

    def test_uses_hardest_effort_last_20_min(self):
        start = datetime(2026, 7, 20, 10, 0)
        workout = {
            "type": "Running", "start": start, "end": start + timedelta(minutes=40),
            "duration": 40.0,
        }
        # 40 min of HR readings at 170 bpm; last 20 min avg = 170 → LTHR = 170.
        hr = self._hr_series(start, 41, 170)
        assert _estimate_lthr([workout], hr) == 170

    def test_ignores_short_efforts(self):
        start = datetime(2026, 7, 20, 10, 0)
        workout = {
            "type": "Running", "start": start, "end": start + timedelta(minutes=20),
            "duration": 20.0,  # < 30 min → not a candidate
        }
        hr = self._hr_series(start, 21, 170)
        # No qualifying effort → falls back to 89% of max HR (170) = 151.
        assert _estimate_lthr([workout], hr) == round(170 * 0.89)

    def test_no_hr_returns_none(self):
        assert _estimate_lthr([], []) is None


class TestBuildTrainingZones:
    def test_produces_hr_pace_and_meta_rows(self):
        start = datetime(2026, 7, 20, 10, 0)
        workouts = [{
            "type": "Running", "start": start, "end": start + timedelta(minutes=40),
            "duration": 40.0,
        }]
        hr = [(start + timedelta(minutes=m), 170) for m in range(41)]
        vo2 = [("2026-07-01 08:00:00", 50.0, "mL/min·kg")]

        rows = build_training_zones(workouts, hr, vo2)
        zone_types = {r[0] for r in rows}
        assert {"HR", "Pace", "Meta"}.issubset(zone_types)

        # 7 HR zones + a Method row.
        hr_zones = [r for r in rows if r[0] == "HR" and r[1] != "Method"]
        assert len(hr_zones) == 7

        # Meta carries the estimated LTHR back out.
        meta = {r[1]: r[2] for r in rows if r[0] == "Meta"}
        assert meta["Estimated LTHR"] == "170"
        assert meta["Latest VO2 Max"] == "50.0"

    def test_no_vo2_skips_pace_zones(self):
        start = datetime(2026, 7, 20, 10, 0)
        workouts = [{
            "type": "Running", "start": start, "end": start + timedelta(minutes=40),
            "duration": 40.0,
        }]
        hr = [(start + timedelta(minutes=m), 170) for m in range(41)]
        rows = build_training_zones(workouts, hr, [])
        assert not any(r[0] == "Pace" for r in rows)


class TestBuildDailySummary:
    def test_joins_signals_by_date(self):
        # workout_rows use the DB tuple layout: start, end, type, duration_min, ...
        workout_rows = [
            ("2026-07-20 07:00:00", "2026-07-20 07:45:00", "Running", 45.0,
             8.0, "km", 400.0, 150.0, 120.0, 175.0),
        ]
        sleep_rows = [
            # date, bed, wake, in_bed, asleep, deep, core, rem, awake, eff
            ("2026-07-20", "2026-07-19 23:00:00", "2026-07-20 07:00:00",
             480.0, 450.0, 90.0, 240.0, 100.0, 20.0, 93.8),
        ]
        hrv = [("2026-07-20 06:00:00", 65.0, "ms")]
        resting_hr = [("2026-07-20", "2026-07-20 06:00:00", 48.0, "bpm")]
        steps_raw = {"2026-07-20": 9000}
        active_cal_raw = {"2026-07-20": 500.0}
        distance_raw = {"2026-07-20": 8.5}

        rows = build_daily_summary(
            workout_rows, sleep_rows, hrv, resting_hr,
            steps_raw, active_cal_raw, distance_raw, days=3,
        )
        by_date = {r[0]: r for r in rows}
        day = by_date["2026-07-20"]
        # (date, workouts, workout_min, distance_km, steps, active_cal,
        #  sleep_min, sleep_eff_pct, hrv_ms, resting_hr_bpm)
        assert day[1] == "Running"
        assert day[2] == 45.0
        assert day[3] == 8.5
        assert day[4] == 9000
        assert day[5] == 500.0
        assert day[6] == 450.0       # time_asleep_min
        assert day[7] == 93.8
        assert day[8] == 65.0
        assert day[9] == 48.0

    def test_empty_inputs_return_empty(self):
        assert build_daily_summary([], [], [], [], {}, {}, {}) == []

    def test_averages_multiple_hrv_readings(self):
        hrv = [
            ("2026-07-20 06:00:00", 60.0, "ms"),
            ("2026-07-20 06:05:00", 70.0, "ms"),
        ]
        rows = build_daily_summary([], [], hrv, [], {}, {}, {}, days=2)
        day = {r[0]: r for r in rows}["2026-07-20"]
        assert day[8] == 65.0


# A minimal Apple Health export with two record types, a workout, and a
# sleep interval — enough to exercise the full parse → write → read path.
_FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData>
  <Me HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexMale"
      HKCharacteristicTypeIdentifierDateOfBirth="1998-03-16"/>
  <Record type="HKQuantityTypeIdentifierStepCount" unit="count"
          startDate="2026-07-20 08:00:00 +0000" endDate="2026-07-20 08:10:00 +0000" value="1200"/>
  <Record type="HKQuantityTypeIdentifierStepCount" unit="count"
          startDate="2026-07-20 09:00:00 +0000" endDate="2026-07-20 09:10:00 +0000" value="800"/>
  <Record type="HKQuantityTypeIdentifierBodyMass" unit="kg"
          startDate="2026-07-20 07:00:00 +0000" endDate="2026-07-20 07:00:00 +0000" value="72.5"/>
  <Record type="HKQuantityTypeIdentifierHeartRate" unit="count/min"
          startDate="2026-07-20 07:05:00 +0000" endDate="2026-07-20 07:05:00 +0000" value="150"/>
  <Record type="HKCategoryTypeIdentifierSleepAnalysis"
          startDate="2026-07-19 23:00:00 +0000" endDate="2026-07-20 06:00:00 +0000"
          value="HKCategoryValueSleepAnalysisAsleepCore"/>
  <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
           duration="45" durationUnit="min" totalDistance="8.0" totalDistanceUnit="km"
           totalEnergyBurned="400" totalEnergyBurnedUnit="kcal"
           startDate="2026-07-20 07:00:00 +0000" endDate="2026-07-20 07:45:00 +0000"/>
</HealthData>
"""


class TestEndToEndIngest:
    def _run_ingest(self, tmp_path):
        xml_file = tmp_path / "export.xml"
        xml_file.write_text(_FIXTURE_XML)
        db_file = str(tmp_path / "health.db")
        data = parse_export(str(xml_file))
        conn = init_db(db_file)
        write_to_db(conn, data)
        conn.close()
        return db_file

    def test_parses_and_writes_managed_tables(self, tmp_path):
        db_file = self._run_ingest(tmp_path)
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        # Steps aggregated to one day.
        steps = conn.execute("SELECT total_steps FROM steps WHERE date = '2026-07-20'").fetchone()
        assert steps["total_steps"] == 2000

        # Weight recorded.
        weight = conn.execute("SELECT weight_kg FROM weight").fetchone()
        assert weight["weight_kg"] == 72.5

        # Workout ingested with HR attributed from the window.
        workout = conn.execute("SELECT * FROM workouts").fetchone()
        assert workout["type"] == "Running"
        assert workout["avg_hr_bpm"] == 150.0

        # Sleep aggregated to the wake date.
        sleep = conn.execute("SELECT * FROM sleep WHERE date = '2026-07-20'").fetchone()
        assert sleep is not None
        assert sleep["time_asleep_min"] >= 400

        conn.close()

    def test_reingest_preserves_manual_tables(self, tmp_path):
        db_file = self._run_ingest(tmp_path)

        # Simulate the agent writing a manual row.
        conn = sqlite3.connect(db_file)
        conn.execute(
            "INSERT INTO meals (date, time, meal, description, calories_kcal, protein_g, carbs_g, fat_g) "
            "VALUES ('2026-07-20', '13:00', 'lunch', 'Test meal', 600, 40, 60, 20)"
        )
        conn.commit()
        conn.close()

        # Re-run ingest against the same DB (full-replace of managed tables).
        data = parse_export(str(tmp_path / "export.xml"))
        conn = init_db(db_file)
        write_to_db(conn, data)
        conn.close()

        conn = sqlite3.connect(db_file)
        meals = conn.execute("SELECT COUNT(*) AS c FROM meals").fetchone()
        assert meals[0] == 1  # manual row survived the re-ingest
        conn.close()

