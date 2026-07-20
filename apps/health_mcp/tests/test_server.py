"""Tests for MCP server tools — uses a per-test temp SQLite DB."""

import sqlite3
import os
import sys

import pytest


@pytest.fixture(autouse=True)
def mock_db(monkeypatch, tmp_path):
    """Give each test a fresh DB file by patching db.DB_PATH."""
    db_file = str(tmp_path / "test_health.db")
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")

    # Create fresh DB with schema
    conn = sqlite3.connect(db_file)
    conn.executescript(open(schema_path).read())
    conn.commit()
    conn.close()

    # Patch the module-level DB_PATH that get_connection() reads
    import db
    monkeypatch.setattr(db, "DB_PATH", db_file)
    yield db_file


class TestLogMeal:
    def test_log_meal_success(self, mock_db):
        from server import log_meal
        result = log_meal(
            date="2026-07-20",
            time="13:30",
            meal="lunch",
            description="Chicken gyros wrap, tzatziki, fries",
            calories_kcal=780,
            protein_g=42,
            carbs_g=68,
            fat_g=35,
            notes="est. medium portion; ±15%",
        )
        assert "Logged lunch" in result
        assert "780 kcal" in result

        # Verify in DB
        import db
        conn = db.get_connection()
        row = conn.execute("SELECT * FROM meals WHERE date = '2026-07-20'").fetchone()
        assert row["description"] == "Chicken gyros wrap, tzatziki, fries"
        assert row["protein_g"] == 42
        conn.close()

    def test_log_meal_invalid_type(self, mock_db):
        from server import log_meal
        result = log_meal(
            date="2026-07-20", time="13:30", meal="brunch",
            description="test", calories_kcal=100, protein_g=10, carbs_g=10, fat_g=5,
        )
        assert "Error" in result


class TestLogAlcoholCaffeine:
    def test_log_new_day(self, mock_db):
        from server import log_alcohol_caffeine
        result = log_alcohol_caffeine(date="2026-07-20", alcohol_drinks=2, caffeine_servings=3)
        assert "Logged" in result
        assert "2 drinks" in result

    def test_accumulates_on_same_day(self, mock_db):
        from server import log_alcohol_caffeine
        log_alcohol_caffeine(date="2026-07-20", alcohol_drinks=1, caffeine_servings=2)
        result = log_alcohol_caffeine(date="2026-07-20", alcohol_drinks=1, caffeine_servings=1)
        assert "Updated" in result
        assert "2.0 drinks" in result or "2 drinks" in result


class TestLogBloodTest:
    def test_log_marker(self, mock_db):
        from server import log_blood_test
        result = log_blood_test(
            date="2026-07-15", marker="Vitamin D", value=85.0,
            unit="nmol/L", ref_range="50-175 nmol/L",
        )
        assert "Vitamin D" in result
        assert "85.0" in result


class TestUpsertSupplement:
    def test_add_new(self, mock_db):
        from server import upsert_supplement
        result = upsert_supplement(
            supplement="Vitamin D3", dose="5000 IU",
            timing="morning", frequency="daily", started="2026-01-01",
        )
        assert "Added" in result

    def test_stop_existing(self, mock_db):
        from server import upsert_supplement
        upsert_supplement(supplement="Creatine", dose="5g", timing="morning",
                         frequency="daily", started="2026-01-01")
        result = upsert_supplement(supplement="Creatine", stopped="2026-07-20")
        assert "Stopped" in result


class TestReadTools:
    def test_get_daily_summary_empty(self, mock_db):
        from server import get_daily_summary
        result = get_daily_summary(days=7)
        assert result == []

    def test_get_daily_summary_with_data(self, mock_db):
        import db
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO daily_summary (date, steps, active_cal) VALUES (?, ?, ?)",
            ("2026-07-20", 8000, 450.0),
        )
        conn.commit()
        conn.close()

        from server import get_daily_summary
        result = get_daily_summary(days=7)
        assert len(result) == 1
        assert result[0]["steps"] == 8000

    def test_get_training_zones(self, mock_db):
        import db
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO training_zones (zone_type, zone, range, purpose) VALUES (?, ?, ?, ?)",
            ("HR", "1", "< 130 bpm", "Recovery"),
        )
        conn.commit()
        conn.close()

        from server import get_training_zones
        result = get_training_zones()
        assert len(result) == 1
        assert result[0]["zone_type"] == "HR"

    def test_get_profile(self, mock_db):
        import db
        conn = db.get_connection()
        conn.execute("INSERT INTO profile (field, value) VALUES (?, ?)", ("Name", "George"))
        conn.commit()
        conn.close()

        from server import get_profile
        result = get_profile()
        assert result["Name"] == "George"

    def test_get_meals(self, mock_db):
        from server import log_meal, get_meals
        log_meal(
            date="2026-07-20", time="08:00", meal="breakfast",
            description="Oats with berries", calories_kcal=350,
            protein_g=12, carbs_g=55, fat_g=8,
        )
        result = get_meals(days=7)
        assert len(result) == 1
        assert result[0]["meal"] == "breakfast"

    def test_get_supplements(self, mock_db):
        from server import upsert_supplement, get_supplements
        upsert_supplement(supplement="Omega-3", dose="1g", timing="evening",
                         frequency="daily", started="2026-01-01")
        result = get_supplements(active_only=True)
        assert len(result) == 1
        assert result[0]["supplement"] == "Omega-3"
