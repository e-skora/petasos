"""Records "sent" mail in a table. Never sends anything anywhere (spec 3.17)."""

from __future__ import annotations

import sqlite3


def record_mail(
    conn: sqlite3.Connection,
    *,
    scope: str,
    ticket_id: int,
    to_address: str,
    subject: str,
    body: str,
    grant_key: str,
    now_s: str,
) -> None:
    """Insert one row keyed by `grant_key` (the idempotency key). `grant_key` is
    UNIQUE, so a retry of the same grant inserts nothing new."""
    conn.execute(
        "INSERT OR IGNORE INTO helpdesk_fake_mail "
        "(scope, ticket_id, to_address, subject, body, grant_key, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (scope, ticket_id, to_address, subject, body, grant_key, now_s),
    )
