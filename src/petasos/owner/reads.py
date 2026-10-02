"""Scoped reads of ticket activity and ticket names for cards: a documented
cross-package read, like `sessions/store.py`'s expire (ARCHITECTURE.md 0.4, spec
4.5, 4.7).
"""

from __future__ import annotations

import sqlite3
from typing import Any

_ROLES = ("owner", "agent", "visitor")


def _author_role(scope: str, author: str) -> str:
    for role in _ROLES:
        if author == f"{scope}-{role}":
            return role
    return "system"


def ticket_activity(conn: sqlite3.Connection, *, scope: str, number: int) -> dict[str, Any]:
    ticket_row = conn.execute(
        "SELECT id FROM tickets WHERE scope=? AND number=?", (scope, number)
    ).fetchone()
    if ticket_row is None:
        return {"notes": [], "mail": [], "refunds": []}
    ticket_id = ticket_row["id"]

    notes = [
        {
            "id": row["id"],
            "author_role": _author_role(scope, row["author"]),
            "text": row["text"],
            "created_at": row["created_at"],
        }
        for row in conn.execute(
            "SELECT id, author, text, created_at FROM notes "
            "WHERE scope=? AND ticket_id=? ORDER BY id",
            (scope, ticket_id),
        ).fetchall()
    ]
    mail = [
        {
            "id": row["id"],
            "to_address": row["to_address"],
            "subject": row["subject"],
            "sent_at": row["sent_at"],
        }
        for row in conn.execute(
            "SELECT id, to_address, subject, sent_at FROM helpdesk_fake_mail "
            "WHERE scope=? AND ticket_id=? ORDER BY id",
            (scope, ticket_id),
        ).fetchall()
    ]
    refunds = [
        {
            "id": row["id"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
            "refunded_at": row["refunded_at"],
        }
        for row in conn.execute(
            "SELECT id, amount_minor, currency, refunded_at FROM helpdesk_fake_refunds "
            "WHERE scope=? AND ticket_id=? ORDER BY id",
            (scope, ticket_id),
        ).fetchall()
    ]
    return {"notes": notes, "mail": mail, "refunds": refunds}


def ticket_meta(conn: sqlite3.Connection, *, scope: str, resource: str) -> tuple[str, str] | None:
    if not resource.startswith("ticket:"):
        return None
    try:
        number = int(resource.split(":", 1)[1])
    except ValueError:
        return None
    row = conn.execute(
        "SELECT customer, subject FROM tickets WHERE scope=? AND number=? AND deleted=0",
        (scope, number),
    ).fetchone()
    if row is None:
        return None
    return (row["customer"], row["subject"])
