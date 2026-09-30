"""Acceptance tests 1, 3, 4, 21, 27, 28 (spec 003): the composition root over the new
`create_app(db, clock=)` signature, exact routing, the lifespan-owned session
manager, and `ping` visible to every identity through a minted visitor session."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.mcp import server as mcp_server_module
from petasos.storage import Database


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


@pytest.fixture
def app(db: Database, frozen_clock):
    return create_app(db, clock=frozen_clock)


def test_healthz_returns_200_without_a_token(app) -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize(
    "make_headers",
    [
        lambda token: {},
        lambda token: {"Authorization": "Bearer wrong-token"},
        lambda token: {"Authorization": f"Bearer {token}", "Origin": "http://example.com"},
    ],
    ids=["no-token", "wrong-token", "valid-token-with-origin"],
)
def test_mcp_refuses_before_the_handshake(monkeypatch, app, make_headers) -> None:
    calls = []
    original_call = mcp_server_module.StreamableHTTPASGIApp.__call__

    async def counting_call(self, scope, receive, send):
        calls.append(1)
        await original_call(self, scope, receive, send)

    monkeypatch.setattr(mcp_server_module.StreamableHTTPASGIApp, "__call__", counting_call)

    with TestClient(app) as client:
        minted = client.post("/session").json()
        token = minted["tokens"]["agent"]
        response = client.post("/mcp", headers=make_headers(token))

    assert response.status_code == 401
    assert response.content == b""
    assert calls == []


def test_mcp_and_trailing_slash_never_redirect(app) -> None:
    with TestClient(app) as client:
        minted = client.post("/session").json()
        token = minted["tokens"]["agent"]
        first = client.post("/mcp", headers={"Authorization": f"Bearer {token}"})
        second = client.post("/mcp/", headers={"Authorization": f"Bearer {token}"})

    assert first.history == []
    assert second.history == []
    assert first.status_code == second.status_code


async def test_ping_visible_to_every_identity_over_streamable_http(app) -> None:
    async with app.router.lifespan_context(app):
        http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
        )
        minted = (await http_client.post("/session")).json()

        for role, token in minted["tokens"].items():
            http_client.headers["Authorization"] = f"Bearer {token}"
            async with (
                streamable_http_client("http://testserver/mcp", http_client=http_client) as (
                    read_stream,
                    write_stream,
                ),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                assert "ping" in [tool.name for tool in tools.tools], role

                result = await session.call_tool("ping", {})
                assert [block.text for block in result.content] == ["pong"]


def test_lifespan_starts_and_stops_cleanly(app, caplog) -> None:
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass

    problems = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert problems == []
