"""Database schema initialization, connection, and query helpers."""

import sqlite3
from pathlib import Path

import pandas as pd

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "billing.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS api_key_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    billing_no TEXT NOT NULL UNIQUE,
    billing_date DATE NOT NULL,
    api_key TEXT NOT NULL,
    model_code TEXT NOT NULL,
    cost_price REAL,
    token_usage INTEGER,
    token_type TEXT,
    requests INTEGER,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_billing_date ON usage_records(billing_date);
CREATE INDEX IF NOT EXISTS idx_billing_model ON usage_records(model_code);
CREATE INDEX IF NOT EXISTS idx_billing_apikey ON usage_records(api_key);

CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    contribution_date DATE NOT NULL,
    count INTEGER NOT NULL,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(username, contribution_date)
);

CREATE INDEX IF NOT EXISTS idx_contrib_user ON contributions(username);
CREATE INDEX IF NOT EXISTS idx_contrib_date ON contributions(contribution_date);
"""


def get_conn() -> sqlite3.Connection:
    """Return a connection to the SQLite database, creating it if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    conn = get_conn()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def get_distinct_models() -> list[str]:
    """Return sorted list of distinct model_code values."""
    conn = get_conn()
    try:
        cur = conn.execute("SELECT DISTINCT model_code FROM usage_records ORDER BY model_code")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_distinct_usernames() -> list[str]:
    """Return sorted list of distinct usernames (includes 'Unknown' for unmapped keys)."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT DISTINCT COALESCE(m.username, 'Unknown') AS username "
            "FROM usage_records b "
            "LEFT JOIN api_key_map m ON b.api_key = m.api_key "
            "ORDER BY username"
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_provisioned_user_count() -> int:
    """Return the number of distinct users in api_key_map.

    Used by the renewal deck's adoption funnel — the pool of users who were
    provisioned (regardless of whether they actually used the API).
    """
    conn = get_conn()
    try:
        cur = conn.execute("SELECT COUNT(DISTINCT username) FROM api_key_map")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def get_mapped_usernames() -> list[str]:
    """Return sorted list of usernames from api_key_map (excludes 'Unknown').

    Used by the contribution sync, which needs real GitLab usernames to
    fetch each user's calendar.json.
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT DISTINCT username FROM api_key_map ORDER BY username"
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_filtered_data(
    start_date: str,
    end_date: str,
    models: list[str] | None = None,
    usernames: list[str] | None = None,
) -> pd.DataFrame:
    """Return filtered usage records joined with api_key_map as a DataFrame.

    Args:
        start_date: Start date string (YYYY-MM-DD), inclusive.
        end_date: End date string (YYYY-MM-DD), inclusive.
        models: Optional list of model_code values to filter by.
        usernames: Optional list of usernames to filter by.

    Returns:
        DataFrame with columns: billing_no, billing_date, api_key, model_code,
        cost_price, token_usage, token_type, requests, synced_at, username,
        cost (computed as cost_price * token_usage / 1000).
    """
    query = (
        "SELECT b.*, COALESCE(m.username, 'Unknown') AS username, "
        "b.cost_price * b.token_usage / 1000.0 AS cost "
        "FROM usage_records b "
        "LEFT JOIN api_key_map m ON b.api_key = m.api_key "
        "WHERE b.billing_date BETWEEN :start AND :end"
    )
    params: dict = {"start": start_date, "end": end_date}

    if models:
        placeholders = ", ".join(f":m{i}" for i in range(len(models)))
        query += f" AND b.model_code IN ({placeholders})"
        for i, m in enumerate(models):
            params[f"m{i}"] = m

    if usernames:
        placeholders = ", ".join(f":u{i}" for i in range(len(usernames)))
        query += f" AND COALESCE(m.username, 'Unknown') IN ({placeholders})"
        for i, u in enumerate(usernames):
            params[f"u{i}"] = u

    conn = get_conn()
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def get_contributions_data(
    start_date: str,
    end_date: str,
    usernames: list[str] | None = None,
) -> pd.DataFrame:
    """Return filtered contribution records as a DataFrame.

    Args:
        start_date: Start date string (YYYY-MM-DD), inclusive.
        end_date: End date string (YYYY-MM-DD), inclusive.
        usernames: Optional list of usernames to filter by.

    Returns:
        DataFrame with columns: username, contribution_date, count.
    """
    query = (
        "SELECT username, contribution_date, count "
        "FROM contributions "
        "WHERE contribution_date BETWEEN :start AND :end"
    )
    params: dict = {"start": start_date, "end": end_date}

    if usernames:
        placeholders = ", ".join(f":u{i}" for i in range(len(usernames)))
        query += f" AND username IN ({placeholders})"
        for i, u in enumerate(usernames):
            params[f"u{i}"] = u

    conn = get_conn()
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


# Initialize schema on import
init_db()
