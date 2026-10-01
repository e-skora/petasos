"""Per-session quotas and global abuse ceilings (spec 3.21, 3.22).

Limits are module constants, read through the module object (`quotas.CALLS_PER_HOUR`,
never a copy imported with `from`), so a test can monkeypatch them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

QUOTAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    family TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS abuse_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""

CALLS_PER_HOUR = 120
MINTS_PER_HOUR = 30
MINTS_PER_DAY = 500
MAX_LIVE_SESSIONS = 200


def reserve(
    conn: sqlite3.Connection,
    *,
    scope: str,
    family: str,
    limit: int,
    window: timedelta,
    now: datetime,
) -> bool:
    """Count events of `scope`/`family` newer than `now - window`, and insert one
    more when under `limit`. Must run inside the caller's `BEGIN IMMEDIATE`
    transaction, so two concurrent callers cannot both take the last slot."""
    window_start = (now - window).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM quota_events WHERE scope=? AND family=? AND at>?",
        (scope, family, window_start),
    ).fetchone()
    if row["n"] >= limit:
        return False
    conn.execute(
        "INSERT INTO quota_events (scope, family, at) VALUES (?, ?, ?)",
        (scope, family, now.isoformat()),
    )
    return True


def increment_counter(conn: sqlite3.Connection, name: str, *, by: int = 1) -> None:
    conn.execute(
        "INSERT INTO abuse_counters (name, value) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
        (name, by),
    )


def counter_value(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT value FROM abuse_counters WHERE name=?", (name,)).fetchone()
    return row["value"] if row is not None else 0


def live_session_count(conn: sqlite3.Connection, *, now: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE expires_at>?", (now.isoformat(),)
    ).fetchone()
    return row["n"]
