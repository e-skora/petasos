"""Fake refund, email, and note tools plus their fake effect tables, shared by the
other `test_trust_*.py` modules (plan.md: "Tests define their own fake refund, email,
and note tools, and create their own fake effect tables in the temporary database").
Named to match the `tests/test_trust_*.py` wall pattern; it defines no `test_*`
function itself, so pytest collects zero tests from it.
"""

from __future__ import annotations

import sqlite3

from petasos.storage import Database
from petasos.trust.risk import RiskProfile
from petasos.trust.tools import InvalidArguments, NotFound, Resolved, ToolDefinition

FAKE_TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS fake_tickets (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    customer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fake_refunds (
    idempotency_key TEXT PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fake_mail (
    idempotency_key TEXT PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fake_notes (
    idempotency_key TEXT PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fake_deletions (
    idempotency_key TEXT PRIMARY KEY,
    ticket_id INTEGER NOT NULL
);
"""


def make_fake_db(path) -> Database:
    db = Database(path)
    db.migrate()
    conn = db.connect()
    try:
        conn.executescript(FAKE_TABLES_SCHEMA)
    finally:
        conn.close()
    return db


def seed_ticket(
    db: Database, ticket_id: int, *, version: int = 1, customer: str = "cust-1"
) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO fake_tickets (id, version, customer) VALUES (?, ?, ?)",
            (ticket_id, version, customer),
        )
        conn.commit()
    finally:
        conn.close()


def bump_ticket_version(db: Database, ticket_id: int) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE fake_tickets SET version = version + 1 WHERE id=?", (ticket_id,))
        conn.commit()
    finally:
        conn.close()


def ticket_version(conn: sqlite3.Connection, resource: str) -> int:
    ticket_id = int(resource.split(":", 1)[1])
    row = conn.execute("SELECT version FROM fake_tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        raise NotFound(resource)
    return row["version"]


def _resolve_ticket(conn: sqlite3.Connection, ticket_id: int) -> tuple[str, int]:
    row = conn.execute(
        "SELECT customer, version FROM fake_tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"ticket:{ticket_id}")
    return row["customer"], row["version"]


def validate_refund(args: dict) -> dict:
    try:
        ticket = int(args["ticket"])
        amount_minor = round(float(args["amount"]) * 100)
        currency = str(args["currency"]).upper()
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidArguments(str(exc)) from exc
    if amount_minor <= 0 or len(currency) != 3:
        raise InvalidArguments("bad amount or currency")
    return {"ticket": ticket, "amount_minor": amount_minor, "currency": currency}


def resolve_refund(conn: sqlite3.Connection, validated: dict) -> Resolved:
    customer, version = _resolve_ticket(conn, validated["ticket"])
    return Resolved(
        destination=customer,
        amount_minor=validated["amount_minor"],
        currency=validated["currency"],
        resource=f"ticket:{validated['ticket']}",
        resource_version=version,
    )


def record_refund(conn: sqlite3.Connection, record, idempotency_key: str) -> str:
    conn.execute(
        "INSERT INTO fake_refunds (idempotency_key, ticket_id, amount_minor, currency) VALUES (?, ?, ?, ?)",
        (
            idempotency_key,
            int(record.resource.split(":", 1)[1]),
            record.amount_minor,
            record.currency,
        ),
    )
    return "refund_recorded"


def validate_email(args: dict) -> dict:
    try:
        ticket = int(args["ticket"])
        body = str(args["body"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidArguments(str(exc)) from exc
    return {"ticket": ticket, "body": body}


def resolve_email(conn: sqlite3.Connection, validated: dict) -> Resolved:
    customer, version = _resolve_ticket(conn, validated["ticket"])
    return Resolved(
        destination=customer,
        amount_minor=None,
        currency=None,
        resource=f"ticket:{validated['ticket']}",
        resource_version=version,
    )


def record_email(conn: sqlite3.Connection, record, idempotency_key: str) -> str:
    conn.execute(
        "INSERT INTO fake_mail (idempotency_key, ticket_id, body) VALUES (?, ?, ?)",
        (idempotency_key, int(record.resource.split(":", 1)[1]), record.arguments["body"]),
    )
    return "mail_recorded"


def validate_note(args: dict) -> dict:
    try:
        ticket = int(args["ticket"])
        body = str(args["body"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidArguments(str(exc)) from exc
    return {"ticket": ticket, "body": body}


def resolve_note(conn: sqlite3.Connection, validated: dict) -> Resolved:
    _customer, version = _resolve_ticket(conn, validated["ticket"])
    return Resolved(
        destination=None,
        amount_minor=None,
        currency=None,
        resource=f"ticket:{validated['ticket']}",
        resource_version=version,
    )


def record_note(conn: sqlite3.Connection, record, idempotency_key: str) -> str:
    conn.execute(
        "INSERT INTO fake_notes (idempotency_key, ticket_id, body) VALUES (?, ?, ?)",
        (idempotency_key, int(record.resource.split(":", 1)[1]), record.arguments["body"]),
    )
    return "note_recorded"


def validate_delete(args: dict) -> dict:
    try:
        ticket = int(args["ticket"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidArguments(str(exc)) from exc
    return {"ticket": ticket}


def resolve_delete(conn: sqlite3.Connection, validated: dict) -> Resolved:
    _customer, version = _resolve_ticket(conn, validated["ticket"])
    return Resolved(
        destination=None,
        amount_minor=None,
        currency=None,
        resource=f"ticket:{validated['ticket']}",
        resource_version=version,
    )


def record_delete(conn: sqlite3.Connection, record, idempotency_key: str) -> str:
    conn.execute(
        "INSERT INTO fake_deletions (idempotency_key, ticket_id) VALUES (?, ?)",
        (idempotency_key, int(record.resource.split(":", 1)[1])),
    )
    return "deletion_recorded"


REFUND_TOOL = ToolDefinition(
    name="issue_refund",
    profile=RiskProfile(mutation="external", reversible=False, touches_money=True, deletes=False),
    validate=validate_refund,
    resolve=resolve_refund,
    effect=record_refund,
    current_version=ticket_version,
)

EMAIL_TOOL = ToolDefinition(
    name="reply_to_customer",
    profile=RiskProfile(mutation="external", reversible=False, touches_money=False, deletes=False),
    validate=validate_email,
    resolve=resolve_email,
    effect=record_email,
    current_version=ticket_version,
)

NOTE_TOOL = ToolDefinition(
    name="add_internal_note",
    profile=RiskProfile(mutation="internal", reversible=True, touches_money=False, deletes=False),
    validate=validate_note,
    resolve=resolve_note,
    effect=record_note,
    current_version=ticket_version,
)

DELETE_TOOL = ToolDefinition(
    name="delete_ticket",
    profile=RiskProfile(mutation="internal", reversible=False, touches_money=False, deletes=True),
    validate=validate_delete,
    resolve=resolve_delete,
    effect=record_delete,
    current_version=ticket_version,
)


def always_raises_resolve(conn: sqlite3.Connection, validated: dict) -> Resolved:
    raise RuntimeError("boom")


BROKEN_TOOL = ToolDefinition(
    name="broken_tool",
    profile=RiskProfile(mutation="internal", reversible=True, touches_money=False, deletes=False),
    validate=lambda args: args,
    resolve=always_raises_resolve,
    effect=record_note,
    current_version=ticket_version,
)
