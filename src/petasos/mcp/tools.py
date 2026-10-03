"""MCP tool adapters: thin maps from an MCP tool call to `Gate.propose`, or for the
owner tools, to `GrantStore` and `Executor` (spec 3.5 to 3.8). No adapter computes
risk, tier, verb, or approver; the one claimed verb `approve` accepts is compared
against the grant's stored verb and never sets one (spec 3.7).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import Context
from mcp_types import CallToolResult, TextContent

from petasos.helpdesk import ReadCapture, build_registry
from petasos.mcp.identity import Identity
from petasos.mcp.outcomes import MCP_SENTENCES
from petasos.mcp.service import ALLOWED_TOOLS, TOOL_ARG_SPEC, approval_card, approve_and_run
from petasos.memory.store import MemoryStore
from petasos.trust import SENTENCES, Gate, Result

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from petasos.storage import Database


class GateFactory:
    def __init__(self, db: Database, clock: Callable[[], datetime]) -> None:
        self._db = db
        self._clock = clock

    def for_call(self, identity: Identity, capture: ReadCapture) -> Gate:
        registry = build_registry(
            self._db, scope=identity.scope, clock=self._clock, capture=capture
        )
        return Gate(
            self._db,
            registry,
            approvers=frozenset({f"{identity.scope}-owner"}),
            scope=identity.scope,
            clock=self._clock,
        )


def result_json(result: Result, data: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.code,
        "explanation": result.sentence,
        "tier": result.tier.value if result.tier is not None else None,
        "verb": result.verb,
        "grant_id": result.grant_id,
    }
    if data is not None:
        payload["data"] = data
    return payload


def tool_result(result: Result, data: Any = None) -> CallToolResult:
    payload = result_json(result, data)
    is_error = result.code.startswith("refused/") or result.code.startswith("failed/")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))], is_error=is_error
    )


def _ok_result(data: Any) -> CallToolResult:
    payload = {
        "status": "ok",
        "explanation": MCP_SENTENCES["ok"],
        "tier": None,
        "verb": None,
        "grant_id": None,
        "data": data,
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))], is_error=False
    )


def mcp_refusal(code: str) -> CallToolResult:
    """A refusal this MCP layer makes on its own, with no Gate involved. `code` is
    either one of this change's own codes or a 001 code the raw shape check reuses
    (`refused/invalid_arguments`), so the sentence is never composed twice."""
    payload = {
        "status": code,
        "explanation": MCP_SENTENCES.get(code) or SENTENCES[code],
        "tier": None,
        "verb": None,
        "grant_id": None,
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))], is_error=True
    )


def _identity(ctx: Context) -> Identity:
    return ctx.request_context.request.state.identity  # type: ignore[no-any-return]


def register_tools(server: MCPServer, *, db: Database, clock: Callable[[], datetime]) -> None:
    factory = GateFactory(db, clock)

    async def list_tickets(ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        capture = ReadCapture()
        result = factory.for_call(identity, capture).propose(
            "list_tickets", {}, proposer=identity.id
        )
        return _ok_result(capture.data) if result.code == "executed" else tool_result(result)

    async def get_ticket(ticket: int, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        capture = ReadCapture()
        result = factory.for_call(identity, capture).propose(
            "get_ticket", {"ticket": ticket}, proposer=identity.id
        )
        return _ok_result(capture.data) if result.code == "executed" else tool_result(result)

    async def add_internal_note(ticket: int, text: str, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        result = factory.for_call(identity, ReadCapture()).propose(
            "add_internal_note", {"ticket": ticket, "text": text}, proposer=identity.id
        )
        return tool_result(result)

    async def reply_to_customer(ticket: int, body: str, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        result = factory.for_call(identity, ReadCapture()).propose(
            "reply_to_customer", {"ticket": ticket, "body": body}, proposer=identity.id
        )
        return tool_result(result)

    async def issue_refund(ticket: int, amount: str, currency: str, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        result = factory.for_call(identity, ReadCapture()).propose(
            "issue_refund",
            {"ticket": ticket, "amount": amount, "currency": currency},
            proposer=identity.id,
        )
        return tool_result(result)

    async def delete_ticket(ticket: int, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        result = factory.for_call(identity, ReadCapture()).propose(
            "delete_ticket", {"ticket": ticket}, proposer=identity.id
        )
        return tool_result(result)

    async def recall(query: str, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        store = MemoryStore(db, scope=identity.scope, clock=clock)
        entries = store.search(query, ceiling=identity.ceiling)
        return _ok_result([{"key": e.key, "tier": e.tier.value, "text": e.text} for e in entries])

    async def list_pending_approvals(ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        if not identity.approver:
            return mcp_refusal("refused/not_allowed")
        gate = factory.for_call(identity, ReadCapture())
        grants = gate.grants.list_pending(viewer=identity.id)
        cards = [approval_card(grant) for grant in grants]
        return _ok_result(cards)

    async def approve(token: str, verb: str, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        if not identity.approver:
            return mcp_refusal("refused/not_allowed")
        gate = factory.for_call(identity, ReadCapture())
        result = approve_and_run(gate, token=token, verb=verb, approver=identity.id)
        return tool_result(result)

    async def abort(token: str, ctx: Context) -> CallToolResult:
        identity = _identity(ctx)
        if not identity.approver:
            return mcp_refusal("refused/not_allowed")
        gate = factory.for_call(identity, ReadCapture())
        result = gate.grants.abort(token=token, approver=identity.id)
        return tool_result(result)

    for fn in (
        list_tickets,
        get_ticket,
        add_internal_note,
        reply_to_customer,
        issue_refund,
        delete_ticket,
        recall,
        list_pending_approvals,
        approve,
        abort,
    ):
        server.add_tool(fn)


__all__ = [
    "ALLOWED_TOOLS",
    "TOOL_ARG_SPEC",
    "GateFactory",
    "mcp_refusal",
    "register_tools",
    "result_json",
    "tool_result",
]
