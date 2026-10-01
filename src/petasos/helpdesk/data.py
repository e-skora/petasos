"""Fictional help-desk tickets, notes, and the per-session seed data (spec 3.11 to
3.13, 3.18). Every customer, email, and body is invented and embeds the session's
scope id, so two sessions' tickets never share a text field.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from petasos.memory.store import MemoryStore, MemoryTier

if TYPE_CHECKING:
    from petasos.storage import Database

HELPDESK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    number INTEGER NOT NULL,
    customer TEXT NOT NULL,
    email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_minor INTEGER,
    currency TEXT,
    version INTEGER NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(scope, number)
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS helpdesk_fake_mail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    to_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    grant_key TEXT NOT NULL UNIQUE,
    sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS helpdesk_fake_refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    grant_key TEXT NOT NULL UNIQUE,
    refunded_at TEXT NOT NULL
);
"""

CANARY_TICKET_NUMBER = 7
CANARY_MEMORY_KEY = "ticket-7.body"

_SEED_TICKETS: tuple[dict[str, object], ...] = (
    {
        "number": 1,
        "customer": "Visitor One ({scope})",
        "email": "visitor-one-{scope}@example.invalid",
        "subject": "Question about my invoice",
        "body": "Session {scope}: could you explain the last line item on my invoice?",
        "status": "open",
        "amount_minor": None,
        "currency": None,
    },
    {
        "number": 2,
        "customer": "Visitor Two ({scope})",
        "email": "visitor-two-{scope}@example.invalid",
        "subject": "Follow-up on my ticket",
        "body": "Session {scope}: following up, has anyone looked at this yet?",
        "status": "open",
        "amount_minor": None,
        "currency": None,
    },
    {
        "number": 3,
        "customer": "Visitor Three ({scope})",
        "email": "visitor-three-{scope}@example.invalid",
        "subject": "Refund request",
        "body": "Session {scope}: I would like a refund of $42.00 for this order.",
        "status": "open",
        "amount_minor": 4200,
        "currency": "USD",
    },
    {
        "number": 4,
        "customer": "Visitor Four ({scope})",
        "email": "visitor-four-{scope}@example.invalid",
        "subject": "Cannot log in",
        "body": "Session {scope}: I have been locked out of my account since yesterday.",
        "status": "open",
        "amount_minor": None,
        "currency": None,
    },
    {
        "number": 5,
        "customer": "Visitor Five ({scope})",
        "email": "visitor-five-{scope}@example.invalid",
        "subject": "Thanks for the help",
        "body": "Session {scope}: this is sorted now, thank you.",
        "status": "resolved",
        "amount_minor": None,
        "currency": None,
    },
    {
        "number": 6,
        "customer": "Visitor Six ({scope})",
        "email": "visitor-six-{scope}@example.invalid",
        "subject": "Shipping delay",
        "body": "Session {scope}: my order has not arrived, can you check on it?",
        "status": "open",
        "amount_minor": None,
        "currency": None,
    },
)

_TICKET_TWO_NOTE = "Session {scope}: checked with billing, no action needed yet."

_CANARY_SUBJECT = "Archived"
_CANARY_CUSTOMER = "Archive ({scope})"
_CANARY_EMAIL = "archive-{scope}@example.invalid"
_CANARY_MEMORY_TEXT = "Session {scope}: internal archive entry, not for release."

_EXTRA_MEMORY: tuple[tuple[str, MemoryTier, str], ...] = (
    ("note.t0", MemoryTier.T0, "Session {scope}: public note, safe to share with anyone."),
    ("note.t1", MemoryTier.T1, "Session {scope}: internal note, staff only."),
    ("note.t3", MemoryTier.T3, "Session {scope}: agent-facing note, not for the visitor."),
    ("note.t4", MemoryTier.T4, "Session {scope}: private note, owner only."),
)


def _insert_ticket(
    conn: sqlite3.Connection,
    *,
    scope: str,
    number: int,
    customer: str,
    email: str,
    subject: str,
    body: str,
    status: str,
    amount_minor: int | None,
    currency: str | None,
    hidden: bool,
    now_s: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO tickets (scope, number, customer, email, subject, body, status, "
        "amount_minor, currency, version, hidden, deleted, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?)",
        (
            scope,
            number,
            customer,
            email,
            subject,
            body,
            status,
            amount_minor,
            currency,
            1 if hidden else 0,
            now_s,
        ),
    )
    return cur.lastrowid


def seed_session(
    conn: sqlite3.Connection, db: Database, scope: str, clock: Callable[[], datetime]
) -> None:
    """Seed one session's fictional tickets and memory entries. Runs inside the
    caller's transaction; opens no transaction of its own (spec 3.13)."""
    now_s = clock().isoformat()

    for template in _SEED_TICKETS:
        ticket_id = _insert_ticket(
            conn,
            scope=scope,
            number=template["number"],
            customer=str(template["customer"]).format(scope=scope),
            email=str(template["email"]).format(scope=scope),
            subject=str(template["subject"]),
            body=str(template["body"]).format(scope=scope),
            status=str(template["status"]),
            amount_minor=template["amount_minor"],
            currency=template["currency"],
            hidden=False,
            now_s=now_s,
        )
        if template["number"] == 2:
            conn.execute(
                "INSERT INTO notes (scope, ticket_id, author, text, created_at) "
                "VALUES (?, ?, 'agent', ?, ?)",
                (scope, ticket_id, _TICKET_TWO_NOTE.format(scope=scope), now_s),
            )

    memory = MemoryStore(db, scope=scope, clock=clock)
    canary_entry = memory.put_in(
        conn, CANARY_MEMORY_KEY, _CANARY_MEMORY_TEXT.format(scope=scope), MemoryTier.T4
    )
    _insert_ticket(
        conn,
        scope=scope,
        number=CANARY_TICKET_NUMBER,
        customer=_CANARY_CUSTOMER.format(scope=scope),
        email=_CANARY_EMAIL.format(scope=scope),
        subject=_CANARY_SUBJECT,
        body=canary_entry.text,
        status="open",
        amount_minor=None,
        currency=None,
        hidden=True,
        now_s=now_s,
    )

    for key, tier, text in _EXTRA_MEMORY:
        memory.put_in(conn, key, text.format(scope=scope), tier)


def ticket_row(conn: sqlite3.Connection, *, scope: str, number: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tickets WHERE scope=? AND number=?", (scope, number)
    ).fetchone()


def ticket_current_version(conn: sqlite3.Connection, scope: str, resource: str) -> int:
    """`current_version` for a `ticket:<number>` resource; `resource == "tickets"`
    (the list view) has no version to protect and is always 0 (spec 3.5a)."""
    if resource == "tickets":
        return 0
    number = int(resource.split(":", 1)[1])
    row = conn.execute(
        "SELECT version FROM tickets WHERE scope=? AND number=?", (scope, number)
    ).fetchone()
    if row is None:
        raise LookupError(resource)
    return row["version"]


def bump_ticket_version(conn: sqlite3.Connection, *, scope: str, number: int) -> None:
    conn.execute(
        "UPDATE tickets SET version = version + 1 WHERE scope=? AND number=?", (scope, number)
    )
