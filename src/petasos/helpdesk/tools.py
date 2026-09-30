"""The six help-desk `ToolDefinition`s: validate, resolve, effect, and
`current_version` closed over one authenticated scope, so every SQL statement they
run is constrained to it (spec 3.5a, 3.8, 3.14 to 3.18). The scope never comes from
a caller's arguments: `validate` treats a key named `scope` like any other unknown
key and refuses it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from petasos.helpdesk import data, fake_email, fake_payments
from petasos.trust import (
    InvalidArguments,
    NotFound,
    Resolved,
    RiskProfile,
    ToolDefinition,
    ToolRegistry,
)

if TYPE_CHECKING:
    from petasos.storage import Database

_MAX_TEXT_LEN = 2000
_MAX_BODY_LEN = 4000
_AMOUNT_RE = re.compile(r"^\d{1,7}\.\d{2}$")


@dataclass
class ReadCapture:
    """A per-call holder a read tool's effect fills with the data it read, released
    by the adapter only once the Gate reports a committed `executed` (spec 3.5a)."""

    data: object | None = field(default=None)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidArguments("ticket must be an integer")
    if value <= 0:
        raise InvalidArguments("ticket must be positive")
    return value


def _validate_no_args(args: Mapping[str, Any]) -> Mapping[str, Any]:
    if args:
        raise InvalidArguments("this tool takes no parameters")
    return {}


def _validate_ticket_only(args: Mapping[str, Any]) -> Mapping[str, Any]:
    if set(args) != {"ticket"}:
        raise InvalidArguments("expected exactly one parameter: ticket")
    return {"ticket": _positive_int(args["ticket"])}


def _validate_note(args: Mapping[str, Any]) -> Mapping[str, Any]:
    if set(args) != {"ticket", "text"}:
        raise InvalidArguments("expected exactly: ticket, text")
    ticket = _positive_int(args["ticket"])
    text = args["text"]
    if not isinstance(text, str) or not (1 <= len(text) <= _MAX_TEXT_LEN):
        raise InvalidArguments(f"text must be 1 to {_MAX_TEXT_LEN} characters")
    return {"ticket": ticket, "text": text}


def _validate_reply(args: Mapping[str, Any]) -> Mapping[str, Any]:
    if set(args) != {"ticket", "body"}:
        raise InvalidArguments("expected exactly: ticket, body")
    ticket = _positive_int(args["ticket"])
    body = args["body"]
    if not isinstance(body, str) or not (1 <= len(body) <= _MAX_BODY_LEN):
        raise InvalidArguments(f"body must be 1 to {_MAX_BODY_LEN} characters")
    return {"ticket": ticket, "body": body}


def _validate_refund(args: Mapping[str, Any]) -> Mapping[str, Any]:
    if set(args) != {"ticket", "amount", "currency"}:
        raise InvalidArguments("expected exactly: ticket, amount, currency")
    ticket = _positive_int(args["ticket"])
    amount = args["amount"]
    if not isinstance(amount, str) or not _AMOUNT_RE.match(amount) or amount == "0.00":
        raise InvalidArguments("amount must be a positive decimal string with two places")
    currency = args["currency"]
    if currency != "USD":
        raise InvalidArguments("currency must be USD")
    return {"ticket": ticket, "amount": amount, "currency": currency}


def _resolve_ticket_row(
    conn: sqlite3.Connection, scope: str, number: int, *, allow_hidden: bool
) -> sqlite3.Row:
    row = data.ticket_row(conn, scope=scope, number=number)
    if row is None or row["deleted"]:
        raise NotFound(f"ticket:{number}")
    if row["hidden"] and not allow_hidden:
        raise NotFound(f"ticket:{number}")
    return row


def _resolve_list_tickets(
    scope: str,
) -> Callable[[sqlite3.Connection, Mapping[str, Any]], Resolved]:
    def resolve(conn: sqlite3.Connection, validated: Mapping[str, Any]) -> Resolved:
        return Resolved(
            destination=None,
            amount_minor=None,
            currency=None,
            resource="tickets",
            resource_version=0,
        )

    return resolve


def _resolve_get_ticket(scope: str) -> Callable[[sqlite3.Connection, Mapping[str, Any]], Resolved]:
    def resolve(conn: sqlite3.Connection, validated: Mapping[str, Any]) -> Resolved:
        row = _resolve_ticket_row(conn, scope, validated["ticket"], allow_hidden=True)
        return Resolved(
            destination=None,
            amount_minor=None,
            currency=None,
            resource=f"ticket:{validated['ticket']}",
            resource_version=row["version"],
        )

    return resolve


def _resolve_write_ticket(
    scope: str, *, destination: bool = False
) -> Callable[[sqlite3.Connection, Mapping[str, Any]], Resolved]:
    def resolve(conn: sqlite3.Connection, validated: Mapping[str, Any]) -> Resolved:
        row = _resolve_ticket_row(conn, scope, validated["ticket"], allow_hidden=False)
        return Resolved(
            destination=row["email"] if destination else None,
            amount_minor=None,
            currency=None,
            resource=f"ticket:{validated['ticket']}",
            resource_version=row["version"],
        )

    return resolve


def _resolve_refund(scope: str) -> Callable[[sqlite3.Connection, Mapping[str, Any]], Resolved]:
    def resolve(conn: sqlite3.Connection, validated: Mapping[str, Any]) -> Resolved:
        row = _resolve_ticket_row(conn, scope, validated["ticket"], allow_hidden=False)
        if row["amount_minor"] is None:
            raise InvalidArguments("this ticket has no refundable amount")
        amount_minor = int(validated["amount"].replace(".", ""))
        if amount_minor > row["amount_minor"]:
            raise InvalidArguments("amount exceeds the ticket's amount")
        if fake_payments.has_refund(conn, scope=scope, ticket_id=row["id"]):
            raise InvalidArguments("this ticket already has a refund")
        return Resolved(
            destination=row["email"],
            amount_minor=amount_minor,
            currency="USD",
            resource=f"ticket:{validated['ticket']}",
            resource_version=row["version"],
        )

    return resolve


def _effect_list_tickets(capture: ReadCapture) -> Callable[[sqlite3.Connection, Any, str], str]:
    def effect(conn: sqlite3.Connection, record: Any, grant_key: str) -> str:
        rows = conn.execute(
            "SELECT number, customer, subject, status, amount_minor, currency, version "
            "FROM tickets WHERE scope=? AND hidden=0 AND deleted=0 ORDER BY number",
            (record.scope,),
        ).fetchall()
        capture.data = [dict(row) for row in rows]
        return "executed"

    return effect


def _effect_get_ticket(capture: ReadCapture) -> Callable[[sqlite3.Connection, Any, str], str]:
    def effect(conn: sqlite3.Connection, record: Any, grant_key: str) -> str:
        number = record.arguments["ticket"]
        row = conn.execute(
            "SELECT id, number, customer, email, subject, body, status, amount_minor, "
            "currency, version FROM tickets WHERE scope=? AND number=?",
            (record.scope, number),
        ).fetchone()
        if row is None:
            capture.data = None
            return "executed"
        ticket = dict(row)
        ticket_id = ticket.pop("id")
        ticket["notes"] = [
            dict(note_row)
            for note_row in conn.execute(
                "SELECT author, text, created_at FROM notes "
                "WHERE scope=? AND ticket_id=? ORDER BY id",
                (record.scope, ticket_id),
            ).fetchall()
        ]
        ticket["mail"] = [
            dict(mail_row)
            for mail_row in conn.execute(
                "SELECT to_address, subject, body, sent_at FROM helpdesk_fake_mail "
                "WHERE scope=? AND ticket_id=? ORDER BY id",
                (record.scope, ticket_id),
            ).fetchall()
        ]
        ticket["refunds"] = [
            dict(refund_row)
            for refund_row in conn.execute(
                "SELECT amount_minor, currency, refunded_at FROM helpdesk_fake_refunds "
                "WHERE scope=? AND ticket_id=? ORDER BY id",
                (record.scope, ticket_id),
            ).fetchall()
        ]
        capture.data = ticket
        return "executed"

    return effect


def _effect_add_note(
    clock: Callable[[], datetime],
) -> Callable[[sqlite3.Connection, Any, str], str]:
    def effect(conn: sqlite3.Connection, record: Any, grant_key: str) -> str:
        number = record.arguments["ticket"]
        row = conn.execute(
            "SELECT id FROM tickets WHERE scope=? AND number=?", (record.scope, number)
        ).fetchone()
        conn.execute(
            "INSERT INTO notes (scope, ticket_id, author, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                record.scope,
                row["id"],
                record.proposer,
                record.arguments["text"],
                clock().isoformat(),
            ),
        )
        data.bump_ticket_version(conn, scope=record.scope, number=number)
        return "executed"

    return effect


def _effect_reply(clock: Callable[[], datetime]) -> Callable[[sqlite3.Connection, Any, str], str]:
    def effect(conn: sqlite3.Connection, record: Any, grant_key: str) -> str:
        number = record.arguments["ticket"]
        row = conn.execute(
            "SELECT id, email FROM tickets WHERE scope=? AND number=?", (record.scope, number)
        ).fetchone()
        fake_email.record_mail(
            conn,
            scope=record.scope,
            ticket_id=row["id"],
            to_address=row["email"],
            subject="A reply to your ticket",
            body=record.arguments["body"],
            grant_key=grant_key,
            now_s=clock().isoformat(),
        )
        data.bump_ticket_version(conn, scope=record.scope, number=number)
        return "executed"

    return effect


def _effect_issue_refund(
    clock: Callable[[], datetime],
) -> Callable[[sqlite3.Connection, Any, str], str]:
    def effect(conn: sqlite3.Connection, record: Any, grant_key: str) -> str:
        number = record.arguments["ticket"]
        row = conn.execute(
            "SELECT id FROM tickets WHERE scope=? AND number=?", (record.scope, number)
        ).fetchone()
        fake_payments.record_refund(
            conn,
            scope=record.scope,
            ticket_id=row["id"],
            amount_minor=record.amount_minor,
            currency=record.currency,
            grant_key=grant_key,
            now_s=clock().isoformat(),
        )
        data.bump_ticket_version(conn, scope=record.scope, number=number)
        return "executed"

    return effect


def _effect_delete(clock: Callable[[], datetime]) -> Callable[[sqlite3.Connection, Any, str], str]:
    def effect(conn: sqlite3.Connection, record: Any, grant_key: str) -> str:
        number = record.arguments["ticket"]
        conn.execute(
            "UPDATE tickets SET deleted=1 WHERE scope=? AND number=?", (record.scope, number)
        )
        data.bump_ticket_version(conn, scope=record.scope, number=number)
        return "executed"

    return effect


def _current_version(scope: str) -> Callable[[sqlite3.Connection, str], int]:
    def current_version(conn: sqlite3.Connection, resource: str) -> int:
        return data.ticket_current_version(conn, scope, resource)

    return current_version


def build_registry(
    db: Database, *, scope: str, clock: Callable[[], datetime], capture: ReadCapture
) -> ToolRegistry:
    """Build the six help-desk `ToolDefinition`s for one call, all closed over
    `scope`. Never cached or shared across calls, requests, or sessions."""
    definitions = [
        ToolDefinition(
            name="list_tickets",
            profile=RiskProfile(
                mutation="none", reversible=True, touches_money=False, deletes=False
            ),
            validate=_validate_no_args,
            resolve=_resolve_list_tickets(scope),
            effect=_effect_list_tickets(capture),
            current_version=_current_version(scope),
        ),
        ToolDefinition(
            name="get_ticket",
            profile=RiskProfile(
                mutation="none", reversible=True, touches_money=False, deletes=False
            ),
            validate=_validate_ticket_only,
            resolve=_resolve_get_ticket(scope),
            effect=_effect_get_ticket(capture),
            current_version=_current_version(scope),
        ),
        ToolDefinition(
            name="add_internal_note",
            profile=RiskProfile(
                mutation="internal", reversible=True, touches_money=False, deletes=False
            ),
            validate=_validate_note,
            resolve=_resolve_write_ticket(scope),
            effect=_effect_add_note(clock),
            current_version=_current_version(scope),
        ),
        ToolDefinition(
            name="reply_to_customer",
            profile=RiskProfile(
                mutation="external", reversible=False, touches_money=False, deletes=False
            ),
            validate=_validate_reply,
            resolve=_resolve_write_ticket(scope, destination=True),
            effect=_effect_reply(clock),
            current_version=_current_version(scope),
        ),
        ToolDefinition(
            name="issue_refund",
            profile=RiskProfile(
                mutation="external", reversible=False, touches_money=True, deletes=False
            ),
            validate=_validate_refund,
            resolve=_resolve_refund(scope),
            effect=_effect_issue_refund(clock),
            current_version=_current_version(scope),
        ),
        ToolDefinition(
            name="delete_ticket",
            profile=RiskProfile(
                mutation="internal", reversible=False, touches_money=False, deletes=True
            ),
            validate=_validate_ticket_only,
            resolve=_resolve_write_ticket(scope),
            effect=_effect_delete(clock),
            current_version=_current_version(scope),
        ),
    ]
    return ToolRegistry(definitions)
