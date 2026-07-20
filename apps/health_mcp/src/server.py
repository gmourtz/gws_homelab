"""
Health MCP Server
-----------------
Exposes typed tools over HTTP (Streamable HTTP transport) for the OpenClaw
health agent to log meals, read health data, and query training zones.

No raw SQL is exposed to the model — all queries are parameterized and
pre-defined to prevent injection.
"""

import os
import re
from contextlib import closing
from datetime import date, datetime, timedelta
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


# ── DB helpers ────────────────────────────────────────────────────────────────

def _cutoff(days: int) -> str:
    """ISO date `days` days before today, for range-filtered reads."""
    return (datetime.now().date() - timedelta(days=days)).isoformat()


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a read query and return rows as dicts. Connection is always closed."""
    with closing(get_connection()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


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
    calories_kcal: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    notes: Optional[str] = None,
) -> str:
    """Log a meal entry. Call after identifying food from a photo or text description.

    Args:
        date: Date in YYYY-MM-DD format
        time: Time in HH:MM 24h format
        meal: One of: breakfast, lunch, dinner, snack, shake
        description: Short ingredient-level description
        calories_kcal: Estimated calories
        protein_g: Protein in grams
        carbs_g: Carbs in grams
        fat_g: Fat in grams
        notes: Assumptions, confidence, portion notes
    """
    valid_meals = {"breakfast", "lunch", "dinner", "snack", "shake"}
    if meal not in valid_meals:
        return f"Error: meal must be one of {valid_meals}, got '{meal}'"

    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO meals (date, time, meal, description, calories_kcal, protein_g, carbs_g, fat_g, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date, time, meal, description, calories_kcal, protein_g, carbs_g, fat_g, notes),
        )
        conn.commit()
    return f"Logged {meal}: {description} ({calories_kcal:g} kcal, {protein_g:g}g protein)"


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
    with closing(get_connection()) as conn:
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
            return f"Updated {date}: {new_alcohol} drinks, {new_caffeine} caffeine"
        else:
            conn.execute(
                "INSERT INTO alcohol_caffeine (date, alcohol_drinks, caffeine_servings, notes) VALUES (?, ?, ?, ?)",
                (date, alcohol_drinks, caffeine_servings, notes),
            )
            conn.commit()
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
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO blood_tests (date, marker, value, unit, ref_range, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (date, marker, value, unit, ref_range, notes),
        )
        conn.commit()
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
    with closing(get_connection()) as conn:
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
                action = "Stopped" if stopped else "Updated"
                return f"{action} supplement: {supplement}"
            return f"No changes to supplement: {supplement}"
        else:
            conn.execute(
                "INSERT INTO supplements (supplement, dose, timing, frequency, started, stopped, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (supplement, dose, timing, frequency, started, stopped, notes),
            )
            conn.commit()
            return f"Added supplement: {supplement} ({dose}, {timing})"


@mcp.tool()
def upsert_known_food(
    name: str,
    calories_kcal: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    brand: Optional[str] = None,
    serving: Optional[str] = None,
    ingredients: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Save or update a regular/packaged food so it can be logged by name later — no photo needed.

    Call when I give you a label for something I eat regularly or say "remember this" / "save this"
    (e.g. a SimmerEats dish, a Huel format). Match on name; existing optional fields are kept when
    an argument is omitted, so you can refresh macros without wiping the ingredient list.

    Args:
        name: Dish/product name, e.g. "SimmerEats Italian Turkey Rigatoni (#15)"
        calories_kcal: Calories for one serving
        protein_g: Protein in grams for one serving
        carbs_g: Carbs in grams for one serving
        fat_g: Fat in grams for one serving
        brand: Brand/maker, e.g. "SimmerEats", "Huel"
        serving: Portion the macros are for, e.g. "400 g pack", "500 ml bottle"
        ingredients: Full ingredient list / allergens (keep allergens in CAPS as on the label)
        notes: Disambiguation hints, MyFitnessPal search term, storage, etc.
    """
    today = date.today().isoformat()
    with closing(get_connection()) as conn:
        existing = conn.execute(
            "SELECT * FROM known_foods WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()

        if existing:
            # Preserve existing optional fields when the caller omits them.
            brand = brand if brand is not None else existing["brand"]
            serving = serving if serving is not None else existing["serving"]
            ingredients = ingredients if ingredients is not None else existing["ingredients"]
            notes = notes if notes is not None else existing["notes"]
            conn.execute(
                "UPDATE known_foods SET brand = ?, serving = ?, calories_kcal = ?, protein_g = ?, "
                "carbs_g = ?, fat_g = ?, ingredients = ?, notes = ?, updated = ? WHERE id = ?",
                (brand, serving, calories_kcal, protein_g, carbs_g, fat_g, ingredients, notes, today, existing["id"]),
            )
            conn.commit()
            return f"Updated known food: {name} ({calories_kcal:g} kcal, {protein_g:g}g protein)"
        else:
            conn.execute(
                "INSERT INTO known_foods (name, brand, serving, calories_kcal, protein_g, carbs_g, fat_g, ingredients, notes, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, brand, serving, calories_kcal, protein_g, carbs_g, fat_g, ingredients, notes, today),
            )
            conn.commit()
            return f"Saved known food: {name} ({calories_kcal:g} kcal, {protein_g:g}g protein)"


# ── Read tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_daily_summary(days: int = 14) -> list[dict]:
    """Get the daily summary for the most recent N days. Use for readiness checks and weekly reviews.

    Args:
        days: Number of days to return (default 14, max 120)
    """
    days = min(days, 120)
    return _query(
        "SELECT * FROM daily_summary ORDER BY date DESC LIMIT ?",
        (days,),
    )


@mcp.tool()
def get_training_zones() -> list[dict]:
    """Get current training zones (HR zones + pace zones + metadata). Always read fresh."""
    return _query("SELECT * FROM training_zones")


@mcp.tool()
def get_recent_workouts(days: int = 14, workout_type: Optional[str] = None) -> list[dict]:
    """Get recent workouts, optionally filtered by type.

    Args:
        days: Look back this many days (default 14)
        workout_type: Filter by type (e.g. "Running", "HighIntensityIntervalTraining"). None = all types.
    """
    cutoff = _cutoff(days)
    if workout_type:
        return _query(
            "SELECT * FROM workouts WHERE start >= ? AND type = ? ORDER BY start DESC",
            (cutoff, workout_type),
        )
    return _query(
        "SELECT * FROM workouts WHERE start >= ? ORDER BY start DESC",
        (cutoff,),
    )


@mcp.tool()
def get_sleep(days: int = 14) -> list[dict]:
    """Get sleep data for recent nights.

    Args:
        days: Number of nights to return (default 14)
    """
    return _query(
        "SELECT * FROM sleep ORDER BY date DESC LIMIT ?",
        (days,),
    )


@mcp.tool()
def get_meals(days: int = 7) -> list[dict]:
    """Get logged meals for recent days.

    Args:
        days: Number of days to look back (default 7)
    """
    return _query(
        "SELECT * FROM meals WHERE date >= ? ORDER BY date DESC, time DESC",
        (_cutoff(days),),
    )


@mcp.tool()
def get_known_foods(
    query: Optional[str] = None,
    limit: int = 25,
    include_ingredients: bool = False,
) -> list[dict]:
    """Look up saved regular foods. Call before estimating or asking about a named or packaged food.

    Results are capped and, by default, omit the (long) ingredient list to keep the response small —
    enough to pick a dish and log its macros. As the library grows, always search by keyword rather
    than pulling the whole list.

    Args:
        query: Words to search. Ranked full-text (FTS5) over name, brand, and ingredients, prefix-matched
            (e.g. "rigat" finds "Rigatoni") and forgiving of extra words. None = most recent.
        limit: Max rows to return (default 25, capped at 100). Narrow with a keyword instead of raising this.
        include_ingredients: Include the full ingredient/allergen text per row. Set True only when the
            ingredients actually matter (e.g. "does my usual have dairy?").
    """
    limit = max(1, min(limit, 100))
    with closing(get_connection()) as conn:
        if query:
            tokens = re.findall(r"\w+", query.lower())
            if tokens:
                # Ranked full-text search (FTS5). Each token is a prefix term, OR-combined so a
                # conversational phrase still matches; bm25 weights name/brand above ingredients.
                match_expr = " OR ".join(f'"{t}"*' for t in tokens)
                rows = conn.execute(
                    "SELECT kf.* FROM known_foods_fts "
                    "JOIN known_foods AS kf ON kf.id = known_foods_fts.rowid "
                    "WHERE known_foods_fts MATCH ? "
                    "ORDER BY bm25(known_foods_fts, 10.0, 5.0, 1.0), kf.updated DESC "
                    "LIMIT ?",
                    (match_expr, limit),
                ).fetchall()
            else:
                rows = []
        else:
            rows = conn.execute(
                "SELECT * FROM known_foods ORDER BY updated DESC, name LIMIT ?",
                (limit,),
            ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        if not include_ingredients:
            item.pop("ingredients", None)
        results.append(item)
    return results


@mcp.tool()
def get_supplements(active_only: bool = True) -> list[dict]:
    """Get supplement list.

    Args:
        active_only: If True, only return supplements without a stopped date.
    """
    if active_only:
        return _query("SELECT * FROM supplements WHERE stopped IS NULL")
    return _query("SELECT * FROM supplements ORDER BY started DESC")


@mcp.tool()
def get_blood_tests(marker: Optional[str] = None, days: int = 365) -> list[dict]:
    """Get blood test results, optionally filtered by marker name.

    Args:
        marker: Filter by marker name (e.g. "Vitamin D"). None = all markers.
        days: Look back this many days (default 365)
    """
    cutoff = _cutoff(days)
    if marker:
        return _query(
            "SELECT * FROM blood_tests WHERE date >= ? AND marker = ? ORDER BY date DESC",
            (cutoff, marker),
        )
    return _query(
        "SELECT * FROM blood_tests WHERE date >= ? ORDER BY date DESC, marker",
        (cutoff,),
    )


@mcp.tool()
def get_alcohol_caffeine(days: int = 30) -> list[dict]:
    """Get alcohol and caffeine log for recent days.

    Args:
        days: Number of days to look back (default 30)
    """
    return _query(
        "SELECT * FROM alcohol_caffeine WHERE date >= ? ORDER BY date DESC",
        (_cutoff(days),),
    )


@mcp.tool()
def get_weight(days: int = 90) -> list[dict]:
    """Get weight measurements for recent days.

    Args:
        days: Number of days to look back (default 90)
    """
    return _query(
        "SELECT * FROM weight WHERE datetime >= ? ORDER BY datetime DESC",
        (_cutoff(days),),
    )


@mcp.tool()
def get_profile() -> dict:
    """Get the user's health profile (name, DOB, height, weight, etc.)."""
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT field, value FROM profile").fetchall()
    return {row["field"]: row["value"] for row in rows}


@mcp.tool()
def get_hrv(days: int = 30) -> list[dict]:
    """Get HRV measurements for trend analysis.

    Args:
        days: Number of days to look back (default 30)
    """
    return _query(
        "SELECT * FROM hrv WHERE datetime >= ? ORDER BY datetime DESC",
        (_cutoff(days),),
    )


@mcp.tool()
def get_resting_heart_rate(days: int = 30) -> list[dict]:
    """Get resting heart rate measurements for trend analysis.

    Args:
        days: Number of days to look back (default 30)
    """
    return _query(
        "SELECT * FROM resting_heart_rate WHERE date >= ? ORDER BY datetime DESC",
        (_cutoff(days),),
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    init_db()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
