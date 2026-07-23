#!/usr/bin/env python3
"""
health_to_sqlite.py
-------------------
Parses an Apple Health export.xml and writes all metrics into a SQLite database.
Idempotent: managed tables are cleared and fully rewritten on each run (Apple
Health export always contains the full history). Manual tables (meals,
supplements, blood_tests, alcohol_caffeine, known_foods) are NEVER touched.

Usage:
    python3 health_to_sqlite.py /path/to/apple_health_export/export.xml [--db /path/to/health.db]

If --db is not given, defaults to /data/health.db (the Docker volume mount point).
"""

import sys
import os
import bisect
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from collections import defaultdict
import math
import argparse

# ── Config ────────────────────────────────────────────────────────────────────

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")

# Apple Health record type constants
HK = "HKQuantityTypeIdentifier"
HK_CAT = "HKCategoryTypeIdentifier"

RECORD_TYPES = {
    "heart_rate":        f"{HK}HeartRate",
    "resting_hr":        f"{HK}RestingHeartRate",
    "hrv":               f"{HK}HeartRateVariabilitySDNN",
    "steps":             f"{HK}StepCount",
    "active_calories":   f"{HK}ActiveEnergyBurned",
    "weight":            f"{HK}BodyMass",
    "vo2_max":           f"{HK}VO2Max",
    "respiratory_rate":  f"{HK}RespiratoryRate",
    "blood_oxygen":      f"{HK}OxygenSaturation",
    "body_fat":          f"{HK}BodyFatPercentage",
    "basal_calories":    f"{HK}BasalEnergyBurned",
    "distance_walk_run": f"{HK}DistanceWalkingRunning",
    "daylight":          f"{HK}TimeInDaylight",
    "exercise_time":     f"{HK}AppleExerciseTime",
    "hr_recovery":       f"{HK}HeartRateRecoveryOneMinute",
    "wrist_temp":        f"{HK}AppleSleepingWristTemperature",
    "running_speed":     f"{HK}RunningSpeed",
    "running_power":     f"{HK}RunningPower",
    "running_gct":       f"{HK}RunningGroundContactTime",
    "running_vo":        f"{HK}RunningVerticalOscillation",
    "sleep":             f"{HK_CAT}SleepAnalysis",
}

# Tables that the ingestion script manages (cleared + rewritten each run).
# Manual tables are NEVER touched.
MANAGED_TABLES = [
    "profile", "workouts", "daily_summary", "heart_rate_daily",
    "resting_heart_rate", "hrv", "sleep", "steps", "active_calories",
    "weight", "vo2_max", "respiratory_rate", "blood_oxygen", "body_fat",
    "basal_calories", "distance", "time_in_daylight", "exercise_time",
    "hr_recovery", "wrist_temperature", "running_speed", "running_power",
    "running_ground_contact", "running_vertical_osc", "training_zones",
]

MANUAL_TABLES = ["meals", "supplements", "blood_tests", "alcohol_caffeine", "known_foods"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_dt(s):
    """Parse Apple Health datetime string to datetime object."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def fmt_dt(dt):
    """Format datetime for DB (local, no tz)."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fmt_date(dt):
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d")


def safe_float(v, decimals=2):
    try:
        f = float(v)
        return round(f, decimals) if not math.isnan(f) else None
    except (TypeError, ValueError):
        return None


def sleep_stage_label(value):
    """Map HKCategoryValueSleepAnalysis to human label."""
    mapping = {
        "HKCategoryValueSleepAnalysisAsleep":             "Core",
        "HKCategoryValueSleepAnalysisAsleepUnspecified":   "Core",
        "HKCategoryValueSleepAnalysisAsleepCore":         "Core",
        "HKCategoryValueSleepAnalysisAsleepDeep":         "Deep",
        "HKCategoryValueSleepAnalysisAsleepREM":          "REM",
        "HKCategoryValueSleepAnalysisAwake":              "Awake",
        "HKCategoryValueSleepAnalysisInBed":              "In Bed",
    }
    return mapping.get(value, value)


# ── Parse XML ─────────────────────────────────────────────────────────────────

def parse_export(xml_path):
    """Parse Apple Health export.xml using iterparse (memory-efficient)."""
    print(f"Parsing {xml_path} ...")
    tree = ET.iterparse(xml_path, events=("start",))

    profile = {}
    workouts = []
    heart_rate_raw = []
    resting_hr = []
    hrv = []
    sleep = []
    steps_raw = defaultdict(float)
    active_cal_raw = defaultdict(float)
    weight = []
    vo2_max = []
    respiratory_raw = defaultdict(list)
    blood_oxygen_raw = defaultdict(list)
    body_fat = []
    basal_cal_raw = defaultdict(float)
    distance_raw = defaultdict(float)
    daylight_raw = defaultdict(float)
    exercise_time_raw = defaultdict(float)
    hr_recovery = []
    wrist_temp = []
    running_speed = []
    running_power = []
    running_gct = []
    running_vo = []
    current_workout = None

    for event, elem in tree:
        tag = elem.tag

        if tag == "Me":
            profile = {
                "name":             "George Mourtzinos",
                "dob":              elem.get("HKCharacteristicTypeIdentifierDateOfBirth", "1998-03-16"),
                "sex":              elem.get("HKCharacteristicTypeIdentifierBiologicalSex", "HKBiologicalSexMale").replace("HKBiologicalSex", ""),
                "blood_type":       elem.get("HKCharacteristicTypeIdentifierBloodType", ""),
                "height_m":         "",
                "latest_weight_kg": "",
            }

        elif tag == "Record":
            rtype = elem.get("type", "")
            value = elem.get("value", "")
            unit = elem.get("unit", "")
            start = parse_dt(elem.get("startDate"))
            end = parse_dt(elem.get("endDate"))

            if rtype == f"{HK}Height":
                try:
                    v = float(value)
                except ValueError:
                    v = None
                if v and start and fmt_dt(start) > str(profile.get("_latest_height_date", "")):
                    if unit == "ft":
                        v *= 0.3048
                    elif unit == "in":
                        v *= 0.0254
                    elif unit == "cm":
                        v /= 100
                    profile["height_m"] = round(v, 3)
                    profile["_latest_height_date"] = fmt_dt(start)

            elif rtype == RECORD_TYPES["heart_rate"]:
                heart_rate_raw.append((start, safe_float(value, 1)))

            elif rtype == RECORD_TYPES["resting_hr"]:
                resting_hr.append((fmt_date(start), fmt_dt(start), safe_float(value, 1), unit))

            elif rtype == RECORD_TYPES["hrv"]:
                hrv.append((fmt_dt(start), safe_float(value, 3), unit))

            elif rtype == RECORD_TYPES["steps"]:
                if start:
                    steps_raw[fmt_date(start)] += float(value or 0)

            elif rtype == RECORD_TYPES["active_calories"]:
                if start:
                    active_cal_raw[fmt_date(start)] += float(value or 0)

            elif rtype == RECORD_TYPES["weight"]:
                kg = safe_float(value)
                weight.append((fmt_dt(start), kg, unit))
                if kg and (not profile.get("latest_weight_kg") or fmt_dt(start) > str(profile.get("_latest_weight_date", ""))):
                    profile["latest_weight_kg"] = kg
                    profile["_latest_weight_date"] = fmt_dt(start)

            elif rtype == RECORD_TYPES["vo2_max"]:
                vo2_max.append((fmt_dt(start), safe_float(value, 2), unit))

            elif rtype == RECORD_TYPES["respiratory_rate"]:
                if start:
                    respiratory_raw[fmt_date(start)].append(float(value or 0))

            elif rtype == RECORD_TYPES["blood_oxygen"]:
                if start:
                    pct = float(value or 0) * 100 if float(value or 1) <= 1 else float(value or 0)
                    blood_oxygen_raw[fmt_date(start)].append(pct)

            elif rtype == RECORD_TYPES["body_fat"]:
                pct = float(value or 0) * 100 if float(value or 1) <= 1 else float(value or 0)
                body_fat.append((fmt_dt(start), round(pct, 2), unit))

            elif rtype == RECORD_TYPES["sleep"]:
                if start and end:
                    sleep.append((start, end, sleep_stage_label(value)))

            elif rtype == RECORD_TYPES["basal_calories"]:
                if start:
                    basal_cal_raw[fmt_date(start)] += float(value or 0)

            elif rtype == RECORD_TYPES["distance_walk_run"]:
                if start:
                    distance_raw[fmt_date(start)] += float(value or 0)

            elif rtype == RECORD_TYPES["daylight"]:
                if start:
                    daylight_raw[fmt_date(start)] += float(value or 0)

            elif rtype == RECORD_TYPES["exercise_time"]:
                if start:
                    exercise_time_raw[fmt_date(start)] += float(value or 0)

            elif rtype == RECORD_TYPES["hr_recovery"]:
                hr_recovery.append((fmt_dt(start), safe_float(value, 1), unit))

            elif rtype == RECORD_TYPES["wrist_temp"]:
                wrist_temp.append((fmt_dt(start), safe_float(value, 2), unit))

            elif rtype == RECORD_TYPES["running_speed"]:
                running_speed.append((fmt_dt(start), safe_float(value, 2), unit))

            elif rtype == RECORD_TYPES["running_power"]:
                running_power.append((fmt_dt(start), safe_float(value, 1), unit))

            elif rtype == RECORD_TYPES["running_gct"]:
                running_gct.append((fmt_dt(start), safe_float(value, 1), unit))

            elif rtype == RECORD_TYPES["running_vo"]:
                running_vo.append((fmt_dt(start), safe_float(value, 2), unit))

        elif tag == "Workout":
            w_type = elem.get("workoutActivityType", "").replace("HKWorkoutActivityType", "")
            w_start = parse_dt(elem.get("startDate"))
            w_end = parse_dt(elem.get("endDate"))
            duration = safe_float(elem.get("duration"), 1)
            # Legacy attributes (pre-iOS 16 exports). On modern exports these are
            # absent and get filled from the child WorkoutStatistics streamed next.
            dist = safe_float(elem.get("totalDistance"), 3)
            dist_unit = elem.get("totalDistanceUnit", "")
            cal = safe_float(elem.get("totalEnergyBurned"), 1)

            current_workout = {
                "type": w_type, "start": w_start, "end": w_end,
                "duration": duration, "distance": dist,
                "dist_unit": dist_unit, "calories": cal,
            }
            workouts.append(current_workout)

        elif tag == "WorkoutStatistics":
            # Modern exports (iOS 16+) carry distance/calories in per-workout
            # <WorkoutStatistics> children, not Workout attributes. Start-event
            # order is parent-first, so patch the workout we're currently inside.
            if current_workout is not None:
                s_type = elem.get("type", "")
                if s_type in (f"{HK}DistanceWalkingRunning", f"{HK}DistanceCycling"):
                    if current_workout["distance"] is None:
                        current_workout["distance"] = safe_float(elem.get("sum"), 3)
                        current_workout["dist_unit"] = elem.get("unit", "")
                elif s_type == f"{HK}ActiveEnergyBurned":
                    if current_workout["calories"] is None:
                        current_workout["calories"] = safe_float(elem.get("sum"), 1)

        elem.clear()

    print(f"  Workouts: {len(workouts)}")
    print(f"  Heart rate readings: {len(heart_rate_raw)}")
    print(f"  Sleep intervals: {len(sleep)}")

    return dict(
        profile=profile, workouts=workouts, heart_rate_raw=heart_rate_raw,
        resting_hr=resting_hr, hrv=hrv, sleep=sleep,
        steps_raw=steps_raw, active_cal_raw=active_cal_raw, weight=weight,
        vo2_max=vo2_max, respiratory_raw=respiratory_raw,
        blood_oxygen_raw=blood_oxygen_raw, body_fat=body_fat,
        basal_cal_raw=basal_cal_raw, distance_raw=distance_raw,
        daylight_raw=daylight_raw, exercise_time_raw=exercise_time_raw,
        hr_recovery=hr_recovery, wrist_temp=wrist_temp,
        running_speed=running_speed, running_power=running_power,
        running_gct=running_gct, running_vo=running_vo,
    )


# ── Build + dedup ─────────────────────────────────────────────────────────────

def _dedup_workouts(enriched):
    """Drop duplicate workout records (same session logged by multiple sources)."""
    kept = []
    for w, hr_in in enriched:
        dup_idx = None
        for i, (kw, _) in enumerate(kept):
            if kw["type"] != w["type"]:
                continue
            if not (kw["start"] and kw["end"] and w["start"] and w["end"]):
                continue
            dur_kept = (kw["end"] - kw["start"]).total_seconds()
            dur_new = (w["end"] - w["start"]).total_seconds()
            if abs(dur_kept - dur_new) >= 1:
                continue
            offset = abs((w["start"] - kw["start"]).total_seconds())
            if offset <= 3 * 3600 and abs(offset - 3600 * round(offset / 3600)) < 1:
                dup_idx = i
                break
        if dup_idx is None:
            kept.append((w, hr_in))
        elif len(hr_in) > len(kept[dup_idx][1]):
            kept[dup_idx] = (w, hr_in)
    return kept


def build_workouts(workouts, heart_rate_raw):
    """Return deduplicated workout rows with HR stats."""
    hr_clean = sorted(
        ((dt, v) for dt, v in heart_rate_raw if dt and v is not None),
        key=lambda x: x[0],
    )
    hr_times = [dt for dt, _ in hr_clean]

    def hr_window(w):
        ws, we = w["start"], w["end"]
        if not (ws and we):
            return []
        lo = bisect.bisect_left(hr_times, ws)
        hi = bisect.bisect_right(hr_times, we)
        return [v for _, v in hr_clean[lo:hi]]

    ordered = sorted(workouts, key=lambda x: (x["start"] is None, x["start"] or datetime.min))
    enriched = _dedup_workouts([(w, hr_window(w)) for w in ordered])

    rows = []
    for w, hr_in in enriched:
        avg_hr = round(sum(hr_in) / len(hr_in), 1) if hr_in else None
        min_hr = min(hr_in) if hr_in else None
        max_hr = max(hr_in) if hr_in else None
        rows.append((
            fmt_dt(w["start"]), fmt_dt(w["end"]), w["type"],
            w["duration"], w["distance"], w["dist_unit"],
            w["calories"], avg_hr, min_hr, max_hr,
        ))
    return rows


# ── Sleep aggregation ─────────────────────────────────────────────────────────

SLEEP_SESSION_GAP = timedelta(hours=2)
_ASLEEP_STAGES = ("Core", "Deep", "REM")


def _merge_minutes(intervals):
    """Merge overlapping intervals, return total minutes."""
    if not intervals:
        return 0.0
    ivs = sorted(intervals, key=lambda iv: iv[0])
    total = 0.0
    cur_start, cur_end = ivs[0]
    for s, e in ivs[1:]:
        if s <= cur_end:
            if e > cur_end:
                cur_end = e
        else:
            total += (cur_end - cur_start).total_seconds() / 60
            cur_start, cur_end = s, e
    total += (cur_end - cur_start).total_seconds() / 60
    return total


def _group_sleep_sessions(sleep):
    """Group raw sleep intervals into sessions (gap >= 2h starts new session)."""
    records = sorted((r for r in sleep if r[0] and r[1]), key=lambda r: r[0])
    sessions = []
    current = []
    session_end = None
    for start, end, stage in records:
        if current and (start - session_end) >= SLEEP_SESSION_GAP:
            sessions.append(current)
            current = []
            session_end = None
        current.append((start, end, stage))
        session_end = end if session_end is None else max(session_end, end)
    if current:
        sessions.append(current)
    return sessions


def build_sleep(sleep):
    """One row per night, attributed to wake date, longest session kept."""
    sessions = _group_sleep_sessions(sleep)
    by_wake_date = {}

    for session in sessions:
        all_spans = [(s, e) for s, e, _ in session]
        asleep_spans = [(s, e) for s, e, stg in session if stg in _ASLEEP_STAGES]
        deep_spans = [(s, e) for s, e, stg in session if stg == "Deep"]
        core_spans = [(s, e) for s, e, stg in session if stg == "Core"]
        rem_spans = [(s, e) for s, e, stg in session if stg == "REM"]
        awake_spans = [(s, e) for s, e, stg in session if stg == "Awake"]

        bed_time = min(s for s, _, _ in session)
        wake_time = max(e for _, e, _ in session)
        time_in_bed = _merge_minutes(all_spans)
        time_asleep = _merge_minutes(asleep_spans)
        efficiency = round(time_asleep / time_in_bed * 100, 1) if time_in_bed else None

        stats = {
            "bed_time": bed_time, "wake_time": wake_time,
            "time_in_bed": round(time_in_bed, 1),
            "time_asleep": round(time_asleep, 1),
            "deep": round(_merge_minutes(deep_spans), 1),
            "core": round(_merge_minutes(core_spans), 1),
            "rem": round(_merge_minutes(rem_spans), 1),
            "awake": round(_merge_minutes(awake_spans), 1),
            "efficiency": efficiency,
        }
        wake_date = fmt_date(stats["wake_time"])
        if not wake_date:
            continue
        existing = by_wake_date.get(wake_date)
        if existing is None or stats["time_in_bed"] > existing["time_in_bed"]:
            by_wake_date[wake_date] = stats

    rows = []
    for wake_date, s in by_wake_date.items():
        rows.append((
            wake_date, fmt_dt(s["bed_time"]), fmt_dt(s["wake_time"]),
            s["time_in_bed"], s["time_asleep"], s["deep"],
            s["core"], s["rem"], s["awake"], s["efficiency"],
        ))
    return rows


# ── Training Zones ────────────────────────────────────────────────────────────

_VDOT_TABLE = [
    (30, 8.23, 7.45, 6.65, 6.30, 5.90, 140),
    (35, 7.20, 6.50, 5.78, 5.45, 5.07, 118),
    (40, 6.40, 5.80, 5.08, 4.78, 4.45, 104),
    (45, 5.80, 5.25, 4.55, 4.25, 3.95, 92),
    (50, 5.30, 4.80, 4.08, 3.83, 3.55, 82),
    (55, 4.88, 4.43, 3.72, 3.48, 3.22, 74),
    (60, 4.55, 4.12, 3.40, 3.18, 2.93, 67),
    (65, 4.25, 3.85, 3.13, 2.93, 2.70, 61),
    (70, 4.00, 3.62, 2.92, 2.73, 2.52, 56),
]


def _interp(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _vdot_paces(vdot):
    table = _VDOT_TABLE
    if vdot <= table[0][0]:
        return table[0][1:]
    if vdot >= table[-1][0]:
        return table[-1][1:]
    for i in range(len(table) - 1):
        if table[i][0] <= vdot <= table[i + 1][0]:
            return tuple(
                round(_interp(vdot, table[i][0], table[i + 1][0], table[i][j], table[i + 1][j]), 2)
                for j in range(1, 7)
            )
    return table[-1][1:]


def _min_dec_to_str(val):
    mins = int(val)
    secs = int(round((val - mins) * 60))
    return f"{mins}:{secs:02d}"


def _estimate_lthr(workouts, heart_rate_raw):
    """Estimate LTHR: avg HR of last 20 min of hardest 30+ min effort."""
    # Pre-sort and index for O(log n) lookups via bisect
    hr_clean = sorted(
        ((dt, v) for dt, v in heart_rate_raw if dt and v is not None),
        key=lambda x: x[0],
    )
    if not hr_clean:
        return None
    hr_times = [dt for dt, _ in hr_clean]

    best_lthr = None
    endurance_types = {"Running", "Cycling", "Walking", "Hiking", "Swimming",
                       "CrossCountryRunning", "TrailRunning"}
    candidates = [w for w in workouts
                  if w["type"] in endurance_types
                  and w["duration"] and float(w["duration"]) >= 30
                  and w["start"] and w["end"]]

    for w in candidates:
        ws, we = w["start"], w["end"]
        # Use bisect for O(log n) window lookup instead of linear scan
        lo = bisect.bisect_left(hr_times, ws)
        hi = bisect.bisect_right(hr_times, we)
        hr_in = hr_clean[lo:hi]
        if len(hr_in) < 10:
            continue
        cutoff = we.timestamp() - 20 * 60
        last_20 = [v for dt, v in hr_in if dt.timestamp() >= cutoff]
        if len(last_20) < 5:
            continue
        avg = sum(last_20) / len(last_20)
        if best_lthr is None or avg > best_lthr:
            best_lthr = avg

    if best_lthr:
        return round(best_lthr)

    all_hr = [v for _, v in hr_clean]
    if all_hr:
        return round(max(all_hr) * 0.89)
    return None


def build_training_zones(workouts, heart_rate_raw, vo2_max):
    """Build training zone rows for the training_zones table."""
    rows = []
    lthr = _estimate_lthr(workouts, heart_rate_raw)

    if lthr:
        rows.append(("HR", "Method", f"Joe Friel LTHR-based (LTHR = {lthr} bpm)", "TrainingPeaks standard"))
        zones = [
            ("1", f"< {round(lthr * 0.85)} bpm", "Recovery"),
            ("2", f"{round(lthr * 0.85)}-{round(lthr * 0.89)} bpm", "Aerobic / Endurance"),
            ("3", f"{round(lthr * 0.90)}-{round(lthr * 0.94)} bpm", "Tempo"),
            ("4", f"{round(lthr * 0.95)}-{round(lthr * 0.99)} bpm", "Sub-Threshold"),
            ("5a", f"{round(lthr * 1.00)}-{round(lthr * 1.02)} bpm", "Super-Threshold"),
            ("5b", f"{round(lthr * 1.03)}-{round(lthr * 1.06)} bpm", "Aerobic Capacity (VO2max)"),
            ("5c", f"> {round(lthr * 1.06)} bpm", "Anaerobic Capacity"),
        ]
        for z_name, z_range, z_purpose in zones:
            rows.append(("HR", z_name, z_range, z_purpose))

    vdot = None
    if vo2_max:
        latest = max(vo2_max, key=lambda x: x[0])
        try:
            vdot = float(latest[1])
        except (TypeError, ValueError):
            pass

    if vdot:
        e_slow, e_fast, m, t, i, r_400 = _vdot_paces(vdot)
        rows.append(("Pace", "Method", f"Jack Daniels VDOT (VDOT = {vdot})", "Daniels' Running Formula"))
        paces = [
            ("Easy (E)", f"{_min_dec_to_str(e_fast)}-{_min_dec_to_str(e_slow)} /km", "Base building, long runs, recovery"),
            ("Marathon (M)", f"{_min_dec_to_str(m)} /km", "Race-specific marathon endurance"),
            ("Threshold (T)", f"{_min_dec_to_str(t)} /km", "Lactate clearance, tempo runs"),
            ("Interval (I)", f"{_min_dec_to_str(i)} /km", "VO2max development, 3-5 min repeats"),
            ("Repetition (R)", f"{r_400}s per 400m", "Speed & running economy"),
        ]
        for p_name, p_range, p_purpose in paces:
            rows.append(("Pace", p_name, p_range, p_purpose))

    # Metadata
    all_hr = [v for _, v in heart_rate_raw if v is not None]
    max_hr = max(all_hr) if all_hr else None
    rows.append(("Meta", "Estimated LTHR", str(lthr) if lthr else "-", "Avg HR last 20 min of hardest 30+ min effort"))
    rows.append(("Meta", "Observed Max HR", str(max_hr) if max_hr else "-", "Highest single HR reading"))
    rows.append(("Meta", "Latest VO2 Max", str(vdot) if vdot else "-", "From Apple Watch estimate"))
    rows.append(("Meta", "Computed On", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Recalculated each run"))

    return rows


# ── Daily summary ─────────────────────────────────────────────────────────────

def build_daily_summary(workout_rows, sleep_rows, hrv, resting_hr,
                        steps_raw, active_cal_raw, distance_raw, days=120):
    """Build daily summary rows joining key signals."""
    # Index data by date
    workouts_by_day = defaultdict(list)
    for row in workout_rows:
        d = row[0][:10]  # start datetime → date
        if d:
            workouts_by_day[d].append((row[2], row[3]))  # type, duration

    sleep_by_day = {row[0]: row for row in sleep_rows}  # wake_date → row

    hrv_by_day = defaultdict(list)
    for dt_str, val, _ in hrv:
        if dt_str and val is not None:
            hrv_by_day[dt_str[:10]].append(val)

    rhr_by_day = defaultdict(list)
    for d, _, val, _ in resting_hr:
        if d and val is not None:
            rhr_by_day[d].append(val)

    all_dates = (set(workouts_by_day) | set(sleep_by_day) | set(hrv_by_day)
                 | set(rhr_by_day) | set(steps_raw) | set(active_cal_raw)
                 | set(distance_raw))
    if not all_dates:
        return []

    anchor = datetime.strptime(max(all_dates), "%Y-%m-%d").date()
    rows = []
    for i in range(days):
        d = (anchor - timedelta(days=i)).strftime("%Y-%m-%d")
        wos = workouts_by_day.get(d, [])
        night = sleep_by_day.get(d)
        hrv_vals = hrv_by_day.get(d, [])
        rhr_vals = rhr_by_day.get(d, [])

        rows.append((
            d,
            ", ".join(t for t, _ in wos) if wos else None,
            round(sum(float(dur) for _, dur in wos if dur), 1) if wos else None,
            round(distance_raw[d], 2) if d in distance_raw else None,
            int(steps_raw[d]) if d in steps_raw else None,
            round(active_cal_raw[d], 1) if d in active_cal_raw else None,
            night[4] if night else None,   # time_asleep_min
            night[9] if night else None,   # sleep_eff_pct
            round(sum(hrv_vals) / len(hrv_vals), 1) if hrv_vals else None,
            round(sum(rhr_vals) / len(rhr_vals), 1) if rhr_vals else None,
        ))
    return rows


# ── Write to SQLite ───────────────────────────────────────────────────────────

def init_db(db_path):
    """Create DB and apply schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    schema_sql = open(SCHEMA_PATH).read()
    conn.executescript(schema_sql)
    conn.commit()
    return conn


def write_to_db(conn, data):
    """Clear managed tables and write all parsed data."""
    cur = conn.cursor()

    # Clear managed tables (manual tables are NEVER touched)
    for table in MANAGED_TABLES:
        cur.execute(f"DELETE FROM {table}")  # noqa: S608 — table names are hardcoded constants

    # Profile
    profile = data["profile"]
    h_m = profile.get("height_m", "")
    try:
        h_cm = round(float(h_m) * 100, 1) if h_m else ""
    except (ValueError, TypeError):
        h_cm = ""
    dob_str = profile.get("dob", "")
    try:
        dob_dt = datetime.strptime(dob_str, "%Y-%m-%d")
        today = date.today()
        age = today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
    except ValueError:
        age = ""

    profile_rows = [
        ("Name", profile.get("name", "")),
        ("Date of Birth", dob_str),
        ("Age", str(age)),
        ("Sex", profile.get("sex", "")),
        ("Height (m)", str(h_m)),
        ("Height (cm)", str(h_cm)),
        ("Latest Weight (kg)", str(profile.get("latest_weight_kg", ""))),
        ("Blood Type", profile.get("blood_type", "").replace("HKBloodType", "")),
        ("Last Updated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    cur.executemany("INSERT INTO profile (field, value) VALUES (?, ?)", profile_rows)

    # Workouts (deduplicated, with HR)
    workout_rows = build_workouts(data["workouts"], data["heart_rate_raw"])
    cur.executemany(
        "INSERT INTO workouts (start, end, type, duration_min, distance, distance_unit, calories_kcal, avg_hr_bpm, min_hr_bpm, max_hr_bpm) VALUES (?,?,?,?,?,?,?,?,?,?)",
        workout_rows,
    )

    # Heart rate daily
    daily_hr = defaultdict(list)
    for dt, val in data["heart_rate_raw"]:
        if dt and val is not None:
            daily_hr[fmt_date(dt)].append(val)
    cur.executemany(
        "INSERT INTO heart_rate_daily (date, avg_bpm, min_bpm, max_bpm, readings) VALUES (?,?,?,?,?)",
        [(d, round(sum(v) / len(v), 1), min(v), max(v), len(v)) for d, v in daily_hr.items()],
    )

    # Resting HR
    cur.executemany(
        "INSERT OR IGNORE INTO resting_heart_rate (datetime, date, resting_hr, unit) VALUES (?,?,?,?)",
        data["resting_hr"],
    )

    # HRV
    cur.executemany(
        "INSERT OR IGNORE INTO hrv (datetime, hrv_ms, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["hrv"] if v is not None],
    )

    # Sleep
    sleep_rows = build_sleep(data["sleep"])
    cur.executemany(
        "INSERT OR REPLACE INTO sleep (date, bed_time, wake_time, time_in_bed_min, time_asleep_min, deep_min, core_min, rem_min, awake_min, sleep_eff_pct) VALUES (?,?,?,?,?,?,?,?,?,?)",
        sleep_rows,
    )

    # Steps
    cur.executemany(
        "INSERT INTO steps (date, total_steps) VALUES (?,?)",
        [(d, int(v)) for d, v in data["steps_raw"].items()],
    )

    # Active calories
    cur.executemany(
        "INSERT INTO active_calories (date, calories_kcal) VALUES (?,?)",
        [(d, round(v, 1)) for d, v in data["active_cal_raw"].items()],
    )

    # Weight
    cur.executemany(
        "INSERT OR IGNORE INTO weight (datetime, weight_kg, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["weight"] if v is not None],
    )

    # VO2 Max
    cur.executemany(
        "INSERT OR IGNORE INTO vo2_max (datetime, vo2_max_value, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["vo2_max"] if v is not None],
    )

    # Respiratory rate (daily aggregate)
    cur.executemany(
        "INSERT INTO respiratory_rate (date, avg_brpm, min_brpm, max_brpm, readings) VALUES (?,?,?,?,?)",
        [(d, round(sum(v) / len(v), 2), round(min(v), 2), round(max(v), 2), len(v))
         for d, v in data["respiratory_raw"].items()],
    )

    # Blood oxygen (daily aggregate)
    cur.executemany(
        "INSERT INTO blood_oxygen (date, avg_pct, min_pct, max_pct, readings) VALUES (?,?,?,?,?)",
        [(d, round(sum(v) / len(v), 2), round(min(v), 2), round(max(v), 2), len(v))
         for d, v in data["blood_oxygen_raw"].items()],
    )

    # Body fat
    cur.executemany(
        "INSERT OR IGNORE INTO body_fat (datetime, body_fat_pct, unit) VALUES (?,?,?)",
        data["body_fat"],
    )

    # Basal calories
    cur.executemany(
        "INSERT INTO basal_calories (date, calories_kcal) VALUES (?,?)",
        [(d, round(v, 1)) for d, v in data["basal_cal_raw"].items()],
    )

    # Distance
    cur.executemany(
        "INSERT INTO distance (date, distance_km) VALUES (?,?)",
        [(d, round(v, 2)) for d, v in data["distance_raw"].items()],
    )

    # Time in daylight
    cur.executemany(
        "INSERT INTO time_in_daylight (date, minutes) VALUES (?,?)",
        [(d, round(v, 1)) for d, v in data["daylight_raw"].items()],
    )

    # Exercise time
    cur.executemany(
        "INSERT INTO exercise_time (date, minutes) VALUES (?,?)",
        [(d, round(v, 1)) for d, v in data["exercise_time_raw"].items()],
    )

    # HR recovery
    cur.executemany(
        "INSERT OR IGNORE INTO hr_recovery (datetime, recovery_1min, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["hr_recovery"] if v is not None],
    )

    # Wrist temperature
    cur.executemany(
        "INSERT OR IGNORE INTO wrist_temperature (datetime, temp_celsius, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["wrist_temp"] if v is not None],
    )

    # Running metrics
    cur.executemany(
        "INSERT OR IGNORE INTO running_speed (datetime, speed_kmh, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["running_speed"] if v is not None],
    )
    cur.executemany(
        "INSERT OR IGNORE INTO running_power (datetime, power_w, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["running_power"] if v is not None],
    )
    cur.executemany(
        "INSERT OR IGNORE INTO running_ground_contact (datetime, contact_time_ms, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["running_gct"] if v is not None],
    )
    cur.executemany(
        "INSERT OR IGNORE INTO running_vertical_osc (datetime, vertical_osc_cm, unit) VALUES (?,?,?)",
        [(dt, v, u) for dt, v, u in data["running_vo"] if v is not None],
    )

    # Training zones (derived)
    zone_rows = build_training_zones(data["workouts"], data["heart_rate_raw"], data["vo2_max"])
    cur.executemany(
        "INSERT INTO training_zones (zone_type, zone, range, purpose) VALUES (?,?,?,?)",
        zone_rows,
    )

    # Daily summary (derived from already-written data)
    summary_rows = build_daily_summary(
        workout_rows, sleep_rows, data["hrv"], data["resting_hr"],
        data["steps_raw"], data["active_cal_raw"], data["distance_raw"],
    )
    cur.executemany(
        "INSERT INTO daily_summary (date, workouts, workout_min, distance_km, steps, active_cal, sleep_min, sleep_eff_pct, hrv_ms, resting_hr_bpm) VALUES (?,?,?,?,?,?,?,?,?,?)",
        summary_rows,
    )

    conn.commit()
    print(f"  Written {len(workout_rows)} workouts, {len(sleep_rows)} nights, {len(summary_rows)} daily summaries")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import Apple Health export.xml into SQLite")
    parser.add_argument("xml_path", help="Path to Apple Health export.xml")
    parser.add_argument("--db", default="/data/health.db", help="Path to SQLite database (default: /data/health.db)")
    args = parser.parse_args()

    if not os.path.exists(args.xml_path):
        sys.exit(f"Error: file not found: {args.xml_path}")

    # Ensure DB directory exists
    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    data = parse_export(args.xml_path)

    print("\nWriting to SQLite...")
    conn = init_db(args.db)
    write_to_db(conn, data)
    conn.close()
    print(f"\n✅ Done! Database: {args.db}")


if __name__ == "__main__":
    main()
