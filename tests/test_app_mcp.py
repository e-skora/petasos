"""Acceptance tests 9 to 13 (spec 0.7): the identity middleware, exact routing, the
lifespan-owned session manager, and one authenticated `ping` tool."""

from __future__ import annotations

import logging

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.mcp import server as mcp_server_module

TOKENS = {"agent": "test-token-abc123"}


@pytest.fixture
def app():
    return create_app(TOKENS)


def test_healthz_returns_200_without_a_token(app) -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Bearer {TOKENS['agent']}", "Origin": "http://example.com"},
    ],
    ids=["no-token", "wrong-token", "valid-token-with-origin"],
)
def test_mcp_refuses_before_the_handshake(monkeypatch, headers) -> None:
    calls = []
    original_call = mcp_server_module.StreamableHTTPASGIApp.__call__

    async def counting_call(self, scope, receive, send):
        calls.append(1)
        await original_call(self, scope, receive, send)

    monkeypatch.setattr(mcp_server_module.StreamableHTTPASGIApp, "__call__", counting_call)
    app = create_app(TOKENS)

    with TestClient(app) as client:
        response = client.post("/mcp", headers=headers)

    assert response.status_code == 401
    assert response.content == b""
    assert calls == []


def test_mcp_and_trailing_slash_never_redirect(app) -> None:
    with TestClient(app) as client:
        first = client.post("/mcp", headers={"Authorization": f"Bearer {TOKENS['agent']}"})
        second = client.post("/mcp/", headers={"Authorization": f"Bearer {TOKENS['agent']}"})

    assert first.history == []
    assert second.history == []
    assert first.status_code == second.status_code


async def test_ping_over_streamable_http(app) -> None:
    async with app.router.lifespan_context(app):
        http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TOKENS['agent']}"},
        )
        async with (
            streamable_http_client("http://testserver/mcp", http_client=http_client) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["ping"]

            result = await session.call_tool("ping", {})
            assert [block.text for block in result.content] == ["pong"]


def test_lifespan_starts_and_stops_cleanly(app, caplog) -> None:
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass

    problems = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert problems == []
