-- Health MCP — SQLite schema
-- Mirrors the Google Sheets tabs from health_to_sheets.py.
-- All time-series tables use ISO-8601 strings for dates/datetimes (SQLite has no native date type).
-- Tables are split into "managed" (written by the ingestion script from Apple Health export)
-- and "manual" (written by the MCP tools from the agent/user).

-- ══════════════════════════════════════════════════════════════════════════════
-- MANAGED TABLES (written by health_to_sqlite.py, idempotent full-replace)
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS profile (
    field  TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workouts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    start         TEXT NOT NULL,   -- ISO-8601 datetime
    end           TEXT NOT NULL,
    type          TEXT NOT NULL,   -- e.g. "Running", "HighIntensityIntervalTraining"
    duration_min  REAL,
    distance      REAL,
    distance_unit TEXT,
    calories_kcal REAL,
    avg_hr_bpm    REAL,
    min_hr_bpm    REAL,
    max_hr_bpm    REAL
);
CREATE INDEX IF NOT EXISTS idx_workouts_start ON workouts(start);
CREATE INDEX IF NOT EXISTS idx_workouts_type ON workouts(type);

CREATE TABLE IF NOT EXISTS daily_summary (
    date           TEXT PRIMARY KEY,  -- YYYY-MM-DD
    workouts       TEXT,              -- comma-separated types
    workout_min    REAL,
    distance_km    REAL,
    steps          INTEGER,
    active_cal     REAL,
    sleep_min      REAL,
    sleep_eff_pct  REAL,
    hrv_ms         REAL,
    resting_hr_bpm REAL
);

CREATE TABLE IF NOT EXISTS heart_rate_daily (
    date     TEXT PRIMARY KEY,
    avg_bpm  REAL,
    min_bpm  REAL,
    max_bpm  REAL,
    readings INTEGER
);

CREATE TABLE IF NOT EXISTS resting_heart_rate (
    datetime    TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    resting_hr  REAL NOT NULL,
    unit        TEXT
);
CREATE INDEX IF NOT EXISTS idx_resting_hr_date ON resting_heart_rate(date);

CREATE TABLE IF NOT EXISTS hrv (
    datetime TEXT PRIMARY KEY,
    hrv_ms   REAL NOT NULL,
    unit     TEXT
);

CREATE TABLE IF NOT EXISTS sleep (
    date            TEXT PRIMARY KEY,  -- wake date
    bed_time        TEXT,
    wake_time       TEXT,
    time_in_bed_min REAL,
    time_asleep_min REAL,
    deep_min        REAL,
    core_min        REAL,
    rem_min         REAL,
    awake_min       REAL,
    sleep_eff_pct   REAL
);

CREATE TABLE IF NOT EXISTS steps (
    date        TEXT PRIMARY KEY,
    total_steps INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS active_calories (
    date         TEXT PRIMARY KEY,
    calories_kcal REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS weight (
    datetime   TEXT PRIMARY KEY,
    weight_kg  REAL NOT NULL,
    unit       TEXT
);

CREATE TABLE IF NOT EXISTS vo2_max (
    datetime      TEXT PRIMARY KEY,
    vo2_max_value REAL NOT NULL,
    unit          TEXT
);

CREATE TABLE IF NOT EXISTS respiratory_rate (
    date     TEXT PRIMARY KEY,
    avg_brpm REAL,
    min_brpm REAL,
    max_brpm REAL,
    readings INTEGER
);

CREATE TABLE IF NOT EXISTS blood_oxygen (
    date     TEXT PRIMARY KEY,
    avg_pct  REAL,
    min_pct  REAL,
    max_pct  REAL,
    readings INTEGER
);

CREATE TABLE IF NOT EXISTS body_fat (
    datetime    TEXT PRIMARY KEY,
    body_fat_pct REAL NOT NULL,
    unit        TEXT
);

CREATE TABLE IF NOT EXISTS basal_calories (
    date          TEXT PRIMARY KEY,
    calories_kcal REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS distance (
    date        TEXT PRIMARY KEY,
    distance_km REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS time_in_daylight (
    date        TEXT PRIMARY KEY,
    minutes     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS exercise_time (
    date    TEXT PRIMARY KEY,
    minutes REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hr_recovery (
    datetime       TEXT PRIMARY KEY,
    recovery_1min  REAL NOT NULL,
    unit           TEXT
);

CREATE TABLE IF NOT EXISTS wrist_temperature (
    datetime     TEXT PRIMARY KEY,
    temp_celsius REAL NOT NULL,
    unit         TEXT
);

CREATE TABLE IF NOT EXISTS running_speed (
    datetime  TEXT PRIMARY KEY,
    speed_kmh REAL NOT NULL,
    unit      TEXT
);

CREATE TABLE IF NOT EXISTS running_power (
    datetime TEXT PRIMARY KEY,
    power_w  REAL NOT NULL,
    unit     TEXT
);

CREATE TABLE IF NOT EXISTS running_ground_contact (
    datetime       TEXT PRIMARY KEY,
    contact_time_ms REAL NOT NULL,
    unit           TEXT
);

CREATE TABLE IF NOT EXISTS running_vertical_osc (
    datetime         TEXT PRIMARY KEY,
    vertical_osc_cm  REAL NOT NULL,
    unit             TEXT
);

CREATE TABLE IF NOT EXISTS training_zones (
    zone_type TEXT NOT NULL,   -- "HR", "Pace", "Meta"
    zone      TEXT NOT NULL,
    range     TEXT,
    purpose   TEXT,
    PRIMARY KEY (zone_type, zone)
);

-- ══════════════════════════════════════════════════════════════════════════════
-- MANUAL TABLES (written by MCP tools — agent / user via Telegram)
-- These are NEVER cleared by the ingestion script.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS meals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,     -- YYYY-MM-DD
    time         TEXT NOT NULL,     -- HH:MM
    meal         TEXT NOT NULL,     -- breakfast/lunch/dinner/snack/shake
    description  TEXT NOT NULL,
    calories_kcal INTEGER NOT NULL,
    protein_g    INTEGER NOT NULL,
    carbs_g      INTEGER NOT NULL,
    fat_g        INTEGER NOT NULL,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);

CREATE TABLE IF NOT EXISTS supplements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    supplement TEXT NOT NULL,
    dose       TEXT,
    timing     TEXT,
    frequency  TEXT,
    started    TEXT,              -- YYYY-MM-DD
    stopped    TEXT,              -- YYYY-MM-DD or NULL if active
    notes      TEXT
);

CREATE TABLE IF NOT EXISTS blood_tests (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,      -- YYYY-MM-DD
    marker    TEXT NOT NULL,
    value     REAL NOT NULL,
    unit      TEXT,
    ref_range TEXT,
    notes     TEXT
);
CREATE INDEX IF NOT EXISTS idx_blood_tests_date ON blood_tests(date);

CREATE TABLE IF NOT EXISTS alcohol_caffeine (
    date              TEXT PRIMARY KEY,  -- YYYY-MM-DD
    alcohol_drinks    REAL,
    caffeine_servings REAL,
    notes             TEXT
);
