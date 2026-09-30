"""Records refunds in a table. Never moves money anywhere (spec 3.17)."""

from __future__ import annotations

import sqlite3


def record_refund(
    conn: sqlite3.Connection,
    *,
    scope: str,
    ticket_id: int,
    amount_minor: int,
    currency: str,
    grant_key: str,
    now_s: str,
) -> None:
    """Insert one row keyed by `grant_key` (the idempotency key). `grant_key` is
    UNIQUE, so a retry of the same grant inserts nothing new."""
    conn.execute(
        "INSERT OR IGNORE INTO helpdesk_fake_refunds "
        "(scope, ticket_id, amount_minor, currency, grant_key, refunded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (scope, ticket_id, amount_minor, currency, grant_key, now_s),
    )


def has_refund(conn: sqlite3.Connection, *, scope: str, ticket_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM helpdesk_fake_refunds WHERE scope=? AND ticket_id=? LIMIT 1",
        (scope, ticket_id),
    ).fetchone()
    return row is not None
