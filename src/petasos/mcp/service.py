"""The call logic both front doors share: the tool argument tables, the raw shape
check, the role tool sets, the approval card, and approve-then-run (spec 4.8). Moved
verbatim out of `mcp/tools.py` and `mcp/server.py`; behaviour is unchanged, only the
home of the code moved.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from petasos.trust.record import record_as_dict

if TYPE_CHECKING:
    from petasos.trust import Gate, Grant, Result

_VISITOR_TOOLS = frozenset({"ping", "list_tickets", "get_ticket", "recall"})
_AGENT_TOOLS = _VISITOR_TOOLS | frozenset(
    {"add_internal_note", "reply_to_customer", "issue_refund", "delete_ticket"}
)
_OWNER_TOOLS = _AGENT_TOOLS | frozenset({"list_pending_approvals", "approve", "abort"})
ALLOWED_TOOLS = {"visitor": _VISITOR_TOOLS, "agent": _AGENT_TOOLS, "owner": _OWNER_TOOLS}

TOOL_ARG_SPEC: dict[str, dict[str, type]] = {
    "ping": {},
    "list_tickets": {},
    "get_ticket": {"ticket": int},
    "recall": {"query": str},
    "add_internal_note": {"ticket": int, "text": str},
    "reply_to_customer": {"ticket": int, "body": str},
    "issue_refund": {"ticket": int, "amount": str, "currency": str},
    "delete_ticket": {"ticket": int},
    "list_pending_approvals": {},
    "approve": {"token": str, "verb": str},
    "abort": {"token": str},
}

PROPOSABLE_TOOLS = frozenset(
    {"add_internal_note", "reply_to_customer", "issue_refund", "delete_ticket"}
)


def shape_ok(name: str, arguments: object) -> bool:
    spec = TOOL_ARG_SPEC.get(name)
    if spec is None or not isinstance(arguments, Mapping):
        return False
    if set(arguments) != set(spec):
        return False
    return all(type(arguments[key]) is expected for key, expected in spec.items())


def approval_card(grant: Grant) -> dict[str, Any]:
    return {
        "grant_id": grant.id,
        "token": grant.token,
        "verb": grant.verb,
        "tier": grant.tier.value,
        "record": record_as_dict(grant.record),
        "expires_at": grant.expires_at.isoformat(),
    }


def approve_and_run(gate: Gate, *, token: str, verb: str, approver: str) -> Result:
    result = gate.grants.approve(token=token, verb=verb, approver=approver)
    if result.code == "approved":
        result = gate.executor.run(result.grant_id)
    return result


__all__ = [
    "ALLOWED_TOOLS",
    "PROPOSABLE_TOOLS",
    "TOOL_ARG_SPEC",
    "approval_card",
    "approve_and_run",
    "shape_ok",
]
