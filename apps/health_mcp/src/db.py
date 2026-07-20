"""Health MCP database access layer — shared by server tools and ingestion."""

import sqlite3
import os

DB_PATH = os.environ.get("HEALTH_DB_PATH", "/data/health.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def get_connection() -> sqlite3.Connection:
    """Return a connection with WAL mode and row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Apply schema.sql to ensure all tables exist."""
    conn = get_connection()
    schema_sql = open(SCHEMA_PATH).read()
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
