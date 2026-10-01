"""Full round trips through a real in-process MCP client: tool visibility per role,
the refund-approval journey, scope isolation, rails, shape refusals, the canary
trip, and the reader test (spec 003; acceptance tests 2, 5 to 14, 16, 25 to 29)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from petasos.app import create_app
from petasos.ledger import Ledger
from petasos.sessions import quotas
from petasos.storage import Database


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


@pytest.fixture
def app(db: Database, frozen_clock):
    return create_app(db, clock=frozen_clock)


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


class _Client:
    """One MCP session for one role, sharing the app's ASGI transport."""

    def __init__(self, app, token: str) -> None:
        self._http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def __aenter__(self) -> ClientSession:
        self._stream_ctx = streamable_http_client(
            "http://testserver/mcp", http_client=self._http_client
        )
        read_stream, write_stream = await self._stream_ctx.__aenter__()
        self._session_ctx = ClientSession(read_stream, write_stream)
        session = await self._session_ctx.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, *exc) -> None:
        await self._session_ctx.__aexit__(*exc)
        await self._stream_ctx.__aexit__(*exc)


async def _mint(app) -> dict:
    http_client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    )
    response = await http_client.post("/session")
    assert response.status_code == 201
    return response.json()


async def test_three_identities_see_three_different_tool_lists(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)

        async with _Client(app, minted["tokens"]["visitor"]) as visitor:
            visitor_tools = {t.name for t in (await visitor.list_tools()).tools}
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            agent_tools = {t.name for t in (await agent.list_tools()).tools}
        async with _Client(app, minted["tokens"]["owner"]) as owner:
            owner_tools = {t.name for t in (await owner.list_tools()).tools}

    assert visitor_tools == {"ping", "list_tickets", "get_ticket", "recall"}
    assert agent_tools == visitor_tools | {
        "add_internal_note",
        "reply_to_customer",
        "issue_refund",
        "delete_ticket",
    }
    assert owner_tools == agent_tools | {"list_pending_approvals", "approve", "abort"}


async def test_refund_approval_reader_journey(app, db) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)

        async with _Client(app, minted["tokens"]["agent"]) as agent:
            ticket = await agent.call_tool("get_ticket", {"ticket": 3})
            assert _payload(ticket)["status"] == "ok"

            staged = await agent.call_tool(
                "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}
            )
            staged_payload = _payload(staged)
            assert staged.is_error is False
            assert staged_payload["status"] == "staged"
            assert staged_payload["tier"] == "L5"
            assert "token" not in json.dumps(staged_payload)

            refused = await agent.call_tool("list_pending_approvals", {})
            assert refused.is_error is True
            assert _payload(refused)["status"] == "refused/not_allowed"

        async with _Client(app, minted["tokens"]["owner"]) as owner:
            pending = await owner.call_tool("list_pending_approvals", {})
            card = _payload(pending)["data"][0]
            assert card["record"]["amount_minor"] == 4200
            assert card["record"]["currency"] == "USD"

            executed = await owner.call_tool(
                "approve", {"token": card["token"], "verb": "ISSUE-REFUND"}
            )
            assert _payload(executed)["status"] == "executed"

            again = await owner.call_tool(
                "approve", {"token": card["token"], "verb": "ISSUE-REFUND"}
            )
            assert _payload(again)["status"] == "refused/unknown_or_used_or_expired"

    assert Ledger(db).verify() is None
    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM helpdesk_fake_refunds").fetchall()
    finally:
        conn.close()
    assert len(refunds) == 1
    assert refunds[0]["amount_minor"] == 4200


async def test_wrong_verb_burns_the_grant_and_agent_cannot_approve(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)

        async with _Client(app, minted["tokens"]["agent"]) as agent:
            await agent.call_tool(
                "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}
            )

        async with _Client(app, minted["tokens"]["owner"]) as owner:
            pending = _payload(await owner.call_tool("list_pending_approvals", {}))["data"][0]
            token = pending["token"]

            wrong_verb = await owner.call_tool("approve", {"token": token, "verb": "DELETE-TICKET"})
            assert _payload(wrong_verb)["status"] == "refused/verb_mismatch"

            burned = await owner.call_tool("approve", {"token": token, "verb": "ISSUE-REFUND"})
            assert _payload(burned)["status"] == "refused/unknown_or_used_or_expired"

        async with _Client(app, minted["tokens"]["agent"]) as agent:
            as_agent = await agent.call_tool("approve", {"token": token, "verb": "ISSUE-REFUND"})
            assert as_agent.is_error is True
            assert _payload(as_agent)["status"] == "refused/not_allowed"


async def test_owner_cannot_approve_a_grant_the_owner_proposed(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["owner"]) as owner:
            await owner.call_tool(
                "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}
            )
            card = _payload(await owner.call_tool("list_pending_approvals", {}))["data"][0]
            result = await owner.call_tool(
                "approve", {"token": card["token"], "verb": "ISSUE-REFUND"}
            )
            assert _payload(result)["status"] == "refused/self_approval"


async def test_extra_keys_are_refused_with_no_ledger_row_and_no_grant(app, db) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            before = len(_ledger_rows(db))
            refused = await agent.call_tool(
                "issue_refund",
                {"ticket": 3, "amount": "42.00", "currency": "USD", "risk": "L1"},
            )
            assert refused.is_error is True
            assert _payload(refused)["status"] == "refused/invalid_arguments"
            after = len(_ledger_rows(db))
    assert before == after


async def test_reply_to_customer_rail_fills_after_five_in_an_hour(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            for ticket in range(1, 6):
                result = await agent.call_tool(
                    "reply_to_customer", {"ticket": ticket, "body": "hi"}
                )
                assert _payload(result)["status"] == "staged"
            sixth = await agent.call_tool("reply_to_customer", {"ticket": 6, "body": "hi"})
            assert _payload(sixth)["status"] == "refused/rail_full"


async def test_add_internal_note_executes_at_once(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            result = await agent.call_tool(
                "add_internal_note", {"ticket": 1, "text": "called back"}
            )
            assert _payload(result)["status"] == "executed"


async def test_get_ticket_writes_one_executed_ledger_row_with_no_ticket_field(app, db) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            await agent.call_tool("get_ticket", {"ticket": 1})

    rows = _ledger_rows(db)
    read_rows = [r for r in rows if r["verb"] == "READ"]
    assert len(read_rows) == 1
    assert "ticket" not in json.dumps(read_rows[0]["detail"])


async def test_a_read_that_fails_to_ledger_discards_its_capture(app, monkeypatch) -> None:
    import petasos.trust.executor as executor_module

    original_append = executor_module.ledger_append

    def failing_append(conn, *, kind, **kwargs):
        if kind == "executed":
            raise RuntimeError("simulated ledger failure")
        return original_append(conn, kind=kind, **kwargs)

    monkeypatch.setattr(executor_module, "ledger_append", failing_append)

    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            result = await agent.call_tool("get_ticket", {"ticket": 3})

    assert result.is_error is True
    payload = _payload(result)
    assert payload["status"] == "failed/execution_error"
    assert "data" not in payload
    assert "Visitor Three" not in json.dumps(payload)


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"ticket": True},
        {"ticket": "3"},
        {"ticket": 3.0},
        {"ticket": 3.5},
        {"amount": 42},
    ],
)
async def test_shape_refusals_from_the_middleware(app, args) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            result = await agent.call_tool("get_ticket", args)
            assert result.is_error is True
            assert _payload(result)["status"] == "refused/invalid_arguments"


async def test_misspelled_tool_name_is_not_allowed(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            result = await agent.call_tool("get_tikcet", {"ticket": 1})
            assert result.is_error is True
            assert _payload(result)["status"] == "refused/not_allowed"


async def test_stale_resource_blocks_approval_after_a_note_is_added(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            await agent.call_tool(
                "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}
            )
            await agent.call_tool("add_internal_note", {"ticket": 3, "text": "checking"})

        async with _Client(app, minted["tokens"]["owner"]) as owner:
            card = _payload(await owner.call_tool("list_pending_approvals", {}))["data"][0]
            result = await owner.call_tool(
                "approve", {"token": card["token"], "verb": "ISSUE-REFUND"}
            )
            assert _payload(result)["status"] == "refused/stale_resource"


async def test_two_sessions_are_fully_isolated(app) -> None:
    async with app.router.lifespan_context(app):
        a = await _mint(app)
        b = await _mint(app)

        async with _Client(app, a["tokens"]["agent"]) as agent_a:
            await agent_a.call_tool(
                "issue_refund", {"ticket": 3, "amount": "10.00", "currency": "USD"}
            )

        async with _Client(app, b["tokens"]["owner"]) as owner_b:
            pending_b = _payload(await owner_b.call_tool("list_pending_approvals", {}))["data"]
            assert pending_b == []

        async with _Client(app, a["tokens"]["owner"]) as owner_a:
            ticket_a = _payload(await owner_a.call_tool("get_ticket", {"ticket": 3}))["data"]

        async with _Client(app, b["tokens"]["owner"]) as owner_b:
            ticket_b = _payload(await owner_b.call_tool("get_ticket", {"ticket": 3}))["data"]

        assert ticket_a["customer"] != ticket_b["customer"]
        assert ticket_a["email"] != ticket_b["email"]


async def test_hidden_canary_ticket_trips_the_guard_for_every_identity(app) -> None:
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        for role in ("visitor", "agent", "owner"):
            async with _Client(app, minted["tokens"][role]) as client:
                from mcp.shared.exceptions import MCPError

                with pytest.raises(MCPError) as exc_info:
                    await client.call_tool("get_ticket", {"ticket": 7})
                error = exc_info.value.error
                assert error.code == -32603
                assert error.data["status"] == "refused/guard_tripped"


async def test_call_quota_refuses_the_call_past_the_hourly_limit(app, monkeypatch) -> None:
    monkeypatch.setattr(quotas, "CALLS_PER_HOUR", 2)
    async with app.router.lifespan_context(app):
        minted = await _mint(app)
        async with _Client(app, minted["tokens"]["agent"]) as agent:
            await agent.call_tool("ping", {})
            await agent.call_tool("ping", {})
            third = await agent.call_tool("ping", {})
            assert third.is_error is True
            assert _payload(third)["status"] == "refused/quota"


def _ledger_rows(db: Database) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
    finally:
        conn.close()
    return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]
