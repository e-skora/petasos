"""The owner card: the MCP card plus the ticket's customer and subject, the
record's destination, and a server-built plain summary (spec 4.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from petasos.mcp.service import approval_card

if TYPE_CHECKING:
    from petasos.trust import Grant
    from petasos.trust.record import ActionRecord

_REPLY_SUBJECT = "A reply to your ticket"


def _format_amount(amount_minor: int, currency: str) -> str:
    dollars, cents = divmod(amount_minor, 100)
    return f"${dollars}.{cents:02d} {currency}"


def _ticket_number(resource: str) -> str:
    if ":" in resource:
        return resource.split(":", 1)[1]
    return resource


def _summary(verb: str, record: ActionRecord, customer: str | None) -> str:
    customer_str = customer if customer is not None else "the customer"
    number = _ticket_number(record.resource)
    version = record.resource_version
    if verb == "ISSUE-REFUND":
        amount = _format_amount(record.amount_minor, record.currency)
        return f"Refund {amount} to {customer_str} on ticket {number} (version {version})."
    if verb == "SEND-EMAIL":
        return f"Send an email to {customer_str} on ticket {number} (version {version})."
    if verb == "DELETE-TICKET":
        return f"Delete ticket {number} (version {version}). This cannot be undone."
    return f"Run this action on ticket {number} (version {version})."


def owner_card(grant: Grant, *, meta: tuple[str, str] | None) -> dict[str, Any]:
    card = approval_card(grant)
    customer, subject = meta if meta is not None else (None, None)
    record = grant.record
    card["customer"] = customer
    card["subject"] = subject
    card["destination"] = record.destination
    card["summary"] = _summary(grant.verb, record, customer)
    if grant.verb == "SEND-EMAIL":
        card["outbound_subject"] = _REPLY_SUBJECT
    return card
