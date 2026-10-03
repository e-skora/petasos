"""The origin gate: preflight, cross-origin headers, and the origin refusal (spec
4.3, acceptance test 3, the gate half)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.owner import origins as origins_module
from petasos.owner.origins import OriginGate
from petasos.storage import Database

ALLOWED = frozenset({"https://petasos.io"})


class _Spy:
    def __init__(self) -> None:
        self.called = False

    def app(self):
        async def inner(scope, receive, send):
            self.called = True
            await send(
                {"type": "http.response.start", "status": 200, "headers": [(b"x-seen", b"1")]}
            )
            await send({"type": "http.response.body", "body": b"{}", "more_body": False})

        return inner


@pytest.fixture
def spy() -> _Spy:
    return _Spy()


@pytest.fixture
def gated_app(spy: _Spy):
    inner = spy.app()
    return OriginGate(inner, origins=ALLOWED)


def _client(gated_app) -> TestClient:
    async def lifespan_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            await gated_app(scope, receive, send)

    return TestClient(lifespan_app)


def test_preflight_from_allowed_origin_is_204_and_never_reaches_the_app(spy, gated_app) -> None:
    with _client(gated_app) as client:
        response = client.options(
            "/owner/approvals",
            headers={
                "Origin": "https://petasos.io",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["access-control-allow-origin"] == "https://petasos.io"
    assert response.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert response.headers["access-control-allow-headers"] == "Authorization, Content-Type"
    assert response.headers["access-control-max-age"] == "600"
    assert response.headers["vary"] == "Origin"
    assert spy.called is False


def test_allowed_origin_on_session_and_owner_gets_cors_headers(spy, gated_app) -> None:
    with _client(gated_app) as client:
        resp1 = client.post("/session", headers={"Origin": "https://petasos.io"})
        resp2 = client.get("/owner/tickets", headers={"Origin": "https://petasos.io"})
    for resp in (resp1, resp2):
        assert resp.headers["access-control-allow-origin"] == "https://petasos.io"
        assert resp.headers["vary"] == "Origin"
        assert resp.headers["cache-control"] == "no-store"
    assert spy.called is True


def test_disallowed_origin_is_403_with_exact_body_and_never_reaches_the_app(spy, gated_app) -> None:
    with _client(gated_app) as client:
        response = client.get("/owner/tickets", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.headers["vary"] == "Origin"
    assert response.content == (
        b'{"status":"refused/origin","explanation":'
        b'"This website is not allowed to use the demo\'s browser endpoints.",'
        b'"tier":null,"verb":null,"grant_id":null}'
    )
    assert spy.called is False

    with _client(gated_app) as client:
        preflight = client.options(
            "/owner/tickets",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert preflight.status_code == 403
    assert spy.called is False


def test_no_origin_header_passes_through_untouched(spy, gated_app) -> None:
    with _client(gated_app) as client:
        response = client.get("/owner/tickets")
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert spy.called is True


def test_mcp_path_is_never_gated(spy, gated_app) -> None:
    with _client(gated_app) as client:
        response = client.post("/mcp", headers={"Origin": "https://petasos.io"})
    assert spy.called is True
    assert "access-control-allow-origin" not in response.headers


def test_healthz_is_untouched_with_any_origin(spy, gated_app) -> None:
    with _client(gated_app) as client:
        response = client.get("/healthz", headers={"Origin": "https://petasos.io"})
    assert spy.called is True
    assert "access-control-allow-origin" not in response.headers


def test_empty_origin_set_refuses_every_gated_request_carrying_an_origin() -> None:
    called = {"value": False}

    async def inner(scope, receive, send):
        called["value"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    gated = OriginGate(inner, origins=frozenset())
    client = TestClient(gated)
    response = client.get("/owner/tickets", headers={"Origin": "https://petasos.io"})
    assert response.status_code == 403
    assert called["value"] is False


def test_lifespan_scope_passes_through(spy) -> None:
    inner_called = {"value": False}

    async def inner(scope, receive, send):
        inner_called["value"] = True
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    gated = OriginGate(inner, origins=ALLOWED)
    with TestClient(gated):
        pass
    assert inner_called["value"] is True


def test_full_app_end_to_end_origin_behaviour(monkeypatch, tmp_sqlite, frozen_clock) -> None:
    monkeypatch.setattr(origins_module, "BROWSER_ORIGINS", ALLOWED)
    db = Database(tmp_sqlite)
    db.migrate()
    app = create_app(db, clock=frozen_clock)

    with TestClient(app) as client:
        minted = client.post("/session", headers={"Origin": "https://petasos.io"}).json()
        token = minted["tokens"]["owner"]

        trip = client.get(
            "/owner/tickets/7",
            headers={"Authorization": f"Bearer {token}", "Origin": "https://petasos.io"},
        )
        assert trip.status_code == 409
        assert trip.headers["access-control-allow-origin"] == "https://petasos.io"

        no_slash = client.get("/owner/tickets/3/")
        assert no_slash.status_code == 404
        no_slash2 = client.post("/session/")
        assert no_slash2.status_code == 404
