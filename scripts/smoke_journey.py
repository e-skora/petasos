"""The release check (spec 005 5.10): mint a session, read ticket 3 as the agent,
propose the $42.00 refund, list and approve it as the owner, confirm it executed
once, over MCP. The release workflow runs this after every deploy; it is also the
rollback check. Uses only `httpx2`, `mcp`, and the standard library, and reads no
environment variable. Never prints a token, a bearer string, or a response body on
any path: a failed step prints only the step name and the status code or result
status.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

_JOURNEY_DEADLINE_S = 120.0
_ROLES = ("visitor", "agent", "owner")


@dataclasses.dataclass(frozen=True)
class JourneyReport:
    exit_code: int
    outcome: str
    session_id: str | None


def _step(name: str, detail: str = "") -> None:
    print(f"{name}: {detail}" if detail else name)


def _fail(name: str, detail: str, session_id: str | None) -> JourneyReport:
    _step(name, detail)
    return JourneyReport(exit_code=1, outcome="failed", session_id=session_id)


async def _call_tool(
    client: httpx2.AsyncClient,
    *,
    mcp_url: str,
    tokens: dict[str, str],
    role: str,
    tool: str,
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    client.headers["Authorization"] = f"Bearer {tokens[role]}"
    async with (
        streamable_http_client(mcp_url, http_client=client) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool, arguments or {})
        text = "".join(getattr(block, "text", "") for block in result.content)
        import json

        return json.loads(text)


async def _list_tools(
    client: httpx2.AsyncClient, *, mcp_url: str, tokens: dict[str, str], role: str
) -> list[str]:
    client.headers["Authorization"] = f"Bearer {tokens[role]}"
    async with (
        streamable_http_client(mcp_url, http_client=client) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
        return [tool.name for tool in result.tools]


async def _read_receipt(
    client: httpx2.AsyncClient, *, owner_token: str
) -> tuple[int, list[dict[str, object]]]:
    response = await client.get(
        "/owner/tickets/3", headers={"Authorization": f"Bearer {owner_token}"}
    )
    if response.status_code != 200:
        return response.status_code, []
    refunds = response.json().get("data", {}).get("refunds", [])
    return response.status_code, refunds


def _matching_refunds(refunds: list[dict[str, object]]) -> list[dict[str, object]]:
    return [r for r in refunds if r.get("amount_minor") == 4200 and r.get("currency") == "USD"]


async def _run_steps(client: httpx2.AsyncClient, *, base_url: str) -> JourneyReport:
    try:
        response = await client.get("/healthz")
    except Exception as exc:  # noqa: BLE001 - a connection error is a failed step
        return _fail("healthz", exc.__class__.__name__, None)
    if response.status_code != 200 or response.json() != {"ok": True}:
        return _fail("healthz", f"status {response.status_code}", None)
    _step("healthz", "ok")

    response = await client.post("/session")
    if response.status_code in (429, 503):
        _step("session", f"status {response.status_code}")
        return JourneyReport(exit_code=3, outcome="capacity", session_id=None)
    if response.status_code != 201:
        return _fail("session", f"status {response.status_code}", None)
    minted = response.json()
    session_id = minted.get("session")
    tokens = minted.get("tokens") or {}
    if not isinstance(tokens, dict) or not all(role in tokens for role in _ROLES):
        return _fail("session", "missing a role token", session_id)
    _step("session", "minted")

    mcp_url = f"{base_url}/mcp"

    tool_names = await _list_tools(client, mcp_url=mcp_url, tokens=tokens, role="agent")
    if "issue_refund" not in tool_names or "approve" in tool_names:
        return _fail("agent_tools_list", "unexpected tool set", session_id)
    _step("agent_tools_list", "ok")

    payload = await _call_tool(
        client,
        mcp_url=mcp_url,
        tokens=tokens,
        role="agent",
        tool="get_ticket",
        arguments={"ticket": 3},
    )
    if payload.get("status") != "ok" or (payload.get("data") or {}).get("amount_minor") != 4200:
        return _fail("get_ticket", str(payload.get("status")), session_id)
    _step("get_ticket", "ok")

    payload = await _call_tool(
        client,
        mcp_url=mcp_url,
        tokens=tokens,
        role="agent",
        tool="issue_refund",
        arguments={"ticket": 3, "amount": "42.00", "currency": "USD"},
    )
    if payload.get("status") != "staged" or payload.get("tier") != "L5" or "token" in payload:
        return _fail("issue_refund", str(payload.get("status")), session_id)
    _step("issue_refund", "staged")

    payload = await _call_tool(
        client, mcp_url=mcp_url, tokens=tokens, role="owner", tool="list_pending_approvals"
    )
    cards = payload.get("data") or []
    matching_cards = [c for c in cards if (c.get("record") or {}).get("amount_minor") == 4200]
    if len(matching_cards) != 1:
        return _fail("list_pending_approvals", f"{len(matching_cards)} matching cards", session_id)
    grant_token = matching_cards[0]["token"]
    _step("list_pending_approvals", "one card")

    payload = await _call_tool(
        client,
        mcp_url=mcp_url,
        tokens=tokens,
        role="owner",
        tool="approve",
        arguments={"token": grant_token, "verb": "ISSUE-REFUND"},
    )
    if payload.get("status") != "executed":
        return _fail("approve", str(payload.get("status")), session_id)
    _step("approve", "executed")

    status_code, refunds = await _read_receipt(client, owner_token=tokens["owner"])
    if status_code == 404:
        _step("receipt", "not available")
        return JourneyReport(exit_code=4, outcome="receipt-unavailable", session_id=session_id)
    if status_code != 200 or len(_matching_refunds(refunds)) != 1:
        return _fail("receipt", f"status {status_code}", session_id)
    _step("receipt", "one refund row")

    payload = await _call_tool(
        client,
        mcp_url=mcp_url,
        tokens=tokens,
        role="owner",
        tool="approve",
        arguments={"token": grant_token, "verb": "ISSUE-REFUND"},
    )
    if payload.get("status") != "refused/unknown_or_used_or_expired":
        return _fail("approve_again", str(payload.get("status")), session_id)
    _step("approve_again", "refused")

    status_code, refunds = await _read_receipt(client, owner_token=tokens["owner"])
    if status_code != 200 or len(_matching_refunds(refunds)) != 1:
        return _fail("receipt_again", f"status {status_code}", session_id)
    _step("receipt_again", "still one refund row")

    payload = await _call_tool(
        client, mcp_url=mcp_url, tokens=tokens, role="owner", tool="list_pending_approvals"
    )
    if payload.get("data"):
        return _fail("list_pending_approvals_empty", "not empty", session_id)
    _step("list_pending_approvals_empty", "ok")

    tool_names = await _list_tools(client, mcp_url=mcp_url, tokens=tokens, role="visitor")
    if "issue_refund" in tool_names:
        return _fail("visitor_tools_list", "unexpected tool present", session_id)
    _step("visitor_tools_list", "ok")

    return JourneyReport(exit_code=0, outcome="confirmed", session_id=session_id)


async def run_journey(client: httpx2.AsyncClient, *, base_url: str) -> JourneyReport:
    try:
        return await asyncio.wait_for(
            _run_steps(client, base_url=base_url), timeout=_JOURNEY_DEADLINE_S
        )
    except TimeoutError:
        return _fail("journey", "exceeded the 120 second deadline", None)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python scripts/smoke_journey.py <base_url>", file=sys.stderr)
        return 2
    base_url = args[0].rstrip("/")

    async def _run() -> JourneyReport:
        async with httpx2.AsyncClient(base_url=base_url, trust_env=False, timeout=30.0) as client:
            return await run_journey(client, base_url=base_url)

    report = asyncio.run(_run())
    if report.exit_code == 0:
        print(f"SMOKE OK {base_url} session {report.session_id}")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
