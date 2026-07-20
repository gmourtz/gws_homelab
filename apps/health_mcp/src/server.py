"""
Health MCP Server
-----------------
Exposes typed tools over HTTP (Streamable HTTP transport) for the OpenClaw
health agent to log meals, read health data, and query training zones.

No raw SQL is exposed to the model — all queries are parameterized and
pre-defined to prevent injection.
"""

import os
from datetime import date, datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from db import get_connection, init_db

# ── Server setup ──────────────────────────────────────────────────────────────

PORT = int(os.environ.get("PORT", "8100"))

mcp = FastMCP(
    "Health MCP",
    instructions="Health data logging and retrieval for the health coach agent",
    host="0.0.0.0",
    port=PORT,
)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Container liveness probe — plain 200, avoids the MCP transport's 406-on-GET."""
    return PlainTextResponse("ok")


# ── Write tools (manual tables) ──────────────────────────────────────────────

@mcp.tool()
def log_meal(
    date: str,
    time: str,
    meal: str,
    description: str,
    calories_kcal: int,
    protein_g: int,
    carbs_g: int,
    fat_g: int,
    notes: Optional[str] = None,
) -> str:
    """Log a meal entry. Call after identifying food from a photo or text description.

    Args:
        date: Date in YYYY-MM-DD format
        time: Time in HH:MM 24h format
        meal: One of: breakfast, lunch, dinner, snack, shake
        description: Short ingredient-level description
        calories_kcal: Estimated calories (integer)
        protein_g: Protein in grams (integer)
        carbs_g: Carbs in grams (integer)
        fat_g: Fat in grams (integer)
        notes: Assumptions, confidence, portion notes
    """
    valid_meals = {"breakfast", "lunch", "dinner", "snack", "shake"}
    if meal not in valid_meals:
        return f"Error: meal must be one of {valid_meals}, got '{meal}'"

    conn = get_connection()
    conn.execute(
        "INSERT INTO meals (date, time, meal, description, calories_kcal, protein_g, carbs_g, fat_g, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (date, time, meal, description, calories_kcal, protein_g, carbs_g, fat_g, notes),
    )
    conn.commit()
    conn.close()
    return f"Logged {meal}: {description} ({calories_kcal} kcal, {protein_g}g protein)"


@mcp.tool()
def log_alcohol_caffeine(
    date: str,
    alcohol_drinks: Optional[float] = None,
    caffeine_servings: Optional[float] = None,
    notes: Optional[str] = None,
) -> str:
    """Log or update alcohol/caffeine count for a day. Call when drinks or coffee are mentioned.

    Args:
        date: Date in YYYY-MM-DD format
        alcohol_drinks: Number of alcoholic drinks (can be fractional)
        caffeine_servings: Number of caffeine servings (coffees, energy drinks)
        notes: Additional context
    """
    conn = get_connection()
    # Upsert: add to existing counts if row exists for today
    existing = conn.execute(
        "SELECT alcohol_drinks, caffeine_servings FROM alcohol_caffeine WHERE date = ?",
        (date,),
    ).fetchone()

    if existing:
        new_alcohol = (existing["alcohol_drinks"] or 0) + (alcohol_drinks or 0)
        new_caffeine = (existing["caffeine_servings"] or 0) + (caffeine_servings or 0)
        conn.execute(
            "UPDATE alcohol_caffeine SET alcohol_drinks = ?, caffeine_servings = ?, notes = ? WHERE date = ?",
            (new_alcohol, new_caffeine, notes, date),
        )
        conn.commit()
        conn.close()
        return f"Updated {date}: {new_alcohol} drinks, {new_caffeine} caffeine"
    else:
        conn.execute(
            "INSERT INTO alcohol_caffeine (date, alcohol_drinks, caffeine_servings, notes) VALUES (?, ?, ?, ?)",
            (date, alcohol_drinks, caffeine_servings, notes),
        )
        conn.commit()
        conn.close()
        return f"Logged {date}: {alcohol_drinks or 0} drinks, {caffeine_servings or 0} caffeine"


@mcp.tool()
def log_blood_test(
    date: str,
    marker: str,
    value: float,
    unit: str,
    ref_range: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Log a single blood test marker. Call once per marker from a lab report.

    Args:
        date: Test date in YYYY-MM-DD format
        marker: Name of the marker (e.g. "Vitamin D", "TSH")
        value: Numeric value
        unit: Unit as shown on the lab report
        ref_range: Reference range from the lab (e.g. "50-175 nmol/L")
        notes: Additional context
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO blood_tests (date, marker, value, unit, ref_range, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (date, marker, value, unit, ref_range, notes),
    )
    conn.commit()
    conn.close()
    return f"Logged blood test: {marker} = {value} {unit} ({date})"


@mcp.tool()
def upsert_supplement(
    supplement: str,
    dose: Optional[str] = None,
    timing: Optional[str] = None,
    frequency: Optional[str] = None,
    started: Optional[str] = None,
    stopped: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Add or update a supplement in the reference list. Set stopped date to mark as discontinued.

    Args:
        supplement: Name of the supplement
        dose: Dose per serving (e.g. "5000 IU", "500mg")
        timing: When taken (e.g. "morning", "with food")
        frequency: How often (e.g. "daily", "3x/week")
        started: Start date YYYY-MM-DD
        stopped: Stop date YYYY-MM-DD (set to mark as discontinued)
        notes: Additional context
    """
    conn = get_connection()
    # Check if supplement exists and is active (no stopped date)
    existing = conn.execute(
        "SELECT id FROM supplements WHERE supplement = ? AND stopped IS NULL",
        (supplement,),
    ).fetchone()

    if existing:
        # Update existing active supplement
        updates = []
        params = []
        if dose is not None:
            updates.append("dose = ?")
            params.append(dose)
        if timing is not None:
            updates.append("timing = ?")
            params.append(timing)
        if frequency is not None:
            updates.append("frequency = ?")
            params.append(frequency)
        if stopped is not None:
            updates.append("stopped = ?")
            params.append(stopped)
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if updates:
            params.append(existing["id"])
            conn.execute(f"UPDATE supplements SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            conn.close()
            action = "Stopped" if stopped else "Updated"
            return f"{action} supplement: {supplement}"
        conn.close()
        return f"No changes to supplement: {supplement}"
    else:
        conn.execute(
            "INSERT INTO supplements (supplement, dose, timing, frequency, started, stopped, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (supplement, dose, timing, frequency, started, stopped, notes),
        )
        conn.commit()
        conn.close()
        return f"Added supplement: {supplement} ({dose}, {timing})"


# ── Read tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_daily_summary(days: int = 14) -> list[dict]:
    """Get the daily summary for the most recent N days. Use for readiness checks and weekly reviews.

    Args:
        days: Number of days to return (default 14, max 120)
    """
    days = min(days, 120)
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM daily_summary ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_training_zones() -> list[dict]:
    """Get current training zones (HR zones + pace zones + metadata). Always read fresh."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM training_zones").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_recent_workouts(days: int = 14, workout_type: Optional[str] = None) -> list[dict]:
    """Get recent workouts, optionally filtered by type.

    Args:
        days: Look back this many days (default 14)
        workout_type: Filter by type (e.g. "Running", "HighIntensityIntervalTraining"). None = all types.
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    if workout_type:
        rows = conn.execute(
            "SELECT * FROM workouts WHERE start >= ? AND type = ? ORDER BY start DESC",
            (cutoff, workout_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM workouts WHERE start >= ? ORDER BY start DESC",
            (cutoff,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_sleep(days: int = 14) -> list[dict]:
    """Get sleep data for recent nights.

    Args:
        days: Number of nights to return (default 14)
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sleep ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_meals(days: int = 7) -> list[dict]:
    """Get logged meals for recent days.

    Args:
        days: Number of days to look back (default 7)
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM meals WHERE date >= ? ORDER BY date DESC, time DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_supplements(active_only: bool = True) -> list[dict]:
    """Get supplement list.

    Args:
        active_only: If True, only return supplements without a stopped date.
    """
    conn = get_connection()
    if active_only:
        rows = conn.execute("SELECT * FROM supplements WHERE stopped IS NULL").fetchall()
    else:
        rows = conn.execute("SELECT * FROM supplements ORDER BY started DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_blood_tests(marker: Optional[str] = None, days: int = 365) -> list[dict]:
    """Get blood test results, optionally filtered by marker name.

    Args:
        marker: Filter by marker name (e.g. "Vitamin D"). None = all markers.
        days: Look back this many days (default 365)
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    if marker:
        rows = conn.execute(
            "SELECT * FROM blood_tests WHERE date >= ? AND marker = ? ORDER BY date DESC",
            (cutoff, marker),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM blood_tests WHERE date >= ? ORDER BY date DESC, marker",
            (cutoff,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_alcohol_caffeine(days: int = 30) -> list[dict]:
    """Get alcohol and caffeine log for recent days.

    Args:
        days: Number of days to look back (default 30)
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM alcohol_caffeine WHERE date >= ? ORDER BY date DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_weight(days: int = 90) -> list[dict]:
    """Get weight measurements for recent days.

    Args:
        days: Number of days to look back (default 90)
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM weight WHERE datetime >= ? ORDER BY datetime DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_profile() -> dict:
    """Get the user's health profile (name, DOB, height, weight, etc.)."""
    conn = get_connection()
    rows = conn.execute("SELECT field, value FROM profile").fetchall()
    conn.close()
    return {row["field"]: row["value"] for row in rows}


@mcp.tool()
def get_hrv(days: int = 30) -> list[dict]:
    """Get HRV measurements for trend analysis.

    Args:
        days: Number of days to look back (default 30)
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM hrv WHERE datetime >= ? ORDER BY datetime DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@mcp.tool()
def get_resting_heart_rate(days: int = 30) -> list[dict]:
    """Get resting heart rate measurements for trend analysis.

    Args:
        days: Number of days to look back (default 30)
    """
    conn = get_connection()
    cutoff = (datetime.now().date() - __import__("datetime").timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM resting_heart_rate WHERE date >= ? ORDER BY datetime DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    init_db()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
