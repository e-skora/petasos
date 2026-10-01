"""`IdentityMiddleware`: 401 before the MCP handshake, and the session-id-to-identity
bindings (spec 3.3; acceptance tests 1, 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from petasos.mcp.identity import ClientRegistry
from petasos.mcp.middleware import IdentityMiddleware
from petasos.sessions.store import SessionStore
from petasos.storage import Database


async def _no_receive():
    return {"type": "http.disconnect"}


def _scope(method: str, *, authorization=None, session_id=None, origin=None) -> dict:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if session_id is not None:
        headers.append((b"mcp-session-id", session_id.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return {"type": "http", "method": method, "path": "/mcp", "headers": headers}


def _downstream(assign_session_id: str | None = None):
    seen: dict = {}

    async def app(scope, receive, send) -> None:
        seen["identity"] = (scope.get("state") or {}).get("identity")
        seen["called"] = True
        headers = []
        if assign_session_id is not None:
            headers.append((b"mcp-session-id", assign_session_id.encode()))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": b"{}"})

    return app, seen


async def _call(middleware, scope) -> list[dict]:
    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    await middleware(scope, _no_receive, send)
    return messages


@pytest.fixture
def session(tmp_sqlite: Path, frozen_clock):
    db = Database(tmp_sqlite)
    db.migrate()
    store = SessionStore(db, clock=frozen_clock)
    return db, store.mint()


async def test_unauthenticated_and_malformed_bearers_get_bare_401(session, frozen_clock) -> None:
    db, _minted = session
    registry = ClientRegistry(db)
    app, seen = _downstream()
    middleware = IdentityMiddleware(app, registry, frozen_clock)

    for authorization in (None, "Bearer no-dot", "Bearer .", "Bearer id.", "Bearer wrong-token"):
        seen.clear()
        seen["called"] = False
        messages = await _call(middleware, _scope("POST", authorization=authorization))
        assert messages[0]["status"] == 401
        assert messages[-1]["body"] == b""
        assert seen["called"] is False


async def test_any_origin_header_is_refused_even_with_a_valid_token(session, frozen_clock) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    app, seen = _downstream()
    middleware = IdentityMiddleware(app, registry, frozen_clock)

    token = minted.tokens["agent"]
    messages = await _call(
        middleware, _scope("POST", authorization=f"Bearer {token}", origin="http://example.com")
    )
    assert messages[0]["status"] == 401
    assert seen == {}


async def test_valid_token_reaches_downstream_with_identity_set(session, frozen_clock) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    app, seen = _downstream()
    middleware = IdentityMiddleware(app, registry, frozen_clock)

    token = minted.tokens["agent"]
    messages = await _call(middleware, _scope("POST", authorization=f"Bearer {token}"))
    assert messages[0]["status"] == 200
    assert seen["identity"].role == "agent"


async def test_a_new_session_id_is_bound_to_the_identity_that_initialized_it(
    session, frozen_clock
) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    app, _ = _downstream(assign_session_id="sess-A")
    middleware = IdentityMiddleware(app, registry, frozen_clock)

    await _call(middleware, _scope("POST", authorization=f"Bearer {minted.tokens['agent']}"))

    # A later request on that session id with the SAME identity's token passes.
    same_identity_app, seen = _downstream()
    middleware._app = same_identity_app
    messages = await _call(
        middleware,
        _scope("POST", authorization=f"Bearer {minted.tokens['agent']}", session_id="sess-A"),
    )
    assert messages[0]["status"] == 200
    assert seen["identity"].role == "agent"


async def test_a_request_on_a_bound_session_with_a_different_identity_is_refused(
    session, frozen_clock
) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    app, _ = _downstream(assign_session_id="sess-A")
    middleware = IdentityMiddleware(app, registry, frozen_clock)
    await _call(middleware, _scope("POST", authorization=f"Bearer {minted.tokens['agent']}"))

    messages = await _call(
        middleware,
        _scope("POST", authorization=f"Bearer {minted.tokens['owner']}", session_id="sess-A"),
    )
    assert messages[0]["status"] == 401


@pytest.mark.parametrize("method", ["POST", "GET", "DELETE"])
async def test_any_request_carrying_an_unbound_session_id_is_refused(
    session, frozen_clock, method
) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    app, seen = _downstream()
    middleware = IdentityMiddleware(app, registry, frozen_clock)

    seen["called"] = False
    messages = await _call(
        middleware,
        _scope(method, authorization=f"Bearer {minted.tokens['agent']}", session_id="never-bound"),
    )
    assert messages[0]["status"] == 401
    assert seen["called"] is False


async def test_delete_removes_the_binding(session, frozen_clock) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    app, _ = _downstream(assign_session_id="sess-A")
    middleware = IdentityMiddleware(app, registry, frozen_clock)
    await _call(middleware, _scope("POST", authorization=f"Bearer {minted.tokens['agent']}"))
    assert "sess-A" in middleware._bindings

    plain_app, _ = _downstream()
    middleware._app = plain_app
    messages = await _call(
        middleware,
        _scope("DELETE", authorization=f"Bearer {minted.tokens['agent']}", session_id="sess-A"),
    )
    assert messages[0]["status"] == 200
    assert "sess-A" not in middleware._bindings


async def test_binding_eviction_is_oldest_first(session, frozen_clock) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    token = minted.tokens["agent"]

    plain_app, _ = _downstream()
    middleware = IdentityMiddleware(plain_app, registry, frozen_clock, max_bindings=2)

    for session_id in ("sess-A", "sess-B", "sess-C"):
        assign_app, _ = _downstream(assign_session_id=session_id)
        middleware._app = assign_app
        await _call(middleware, _scope("POST", authorization=f"Bearer {token}"))

    assert set(middleware._bindings) == {"sess-B", "sess-C"}

    middleware._app = plain_app
    evicted = await _call(
        middleware, _scope("POST", authorization=f"Bearer {token}", session_id="sess-A")
    )
    assert evicted[0]["status"] == 401

    reinitialized, _ = _downstream(assign_session_id="sess-A2")
    middleware._app = reinitialized
    fresh = await _call(middleware, _scope("POST", authorization=f"Bearer {token}"))
    assert fresh[0]["status"] == 200


async def test_expired_identity_is_refused_and_its_binding_is_pruned(session, frozen_clock) -> None:
    db, minted = session
    registry = ClientRegistry(db)
    token = minted.tokens["agent"]
    assign_app, _ = _downstream(assign_session_id="sess-A")
    middleware = IdentityMiddleware(assign_app, registry, frozen_clock)
    await _call(middleware, _scope("POST", authorization=f"Bearer {token}"))
    assert "sess-A" in middleware._bindings

    frozen_clock.advance(61 * 60)
    plain_app, seen = _downstream()
    middleware._app = plain_app
    seen["called"] = False
    messages = await _call(
        middleware, _scope("POST", authorization=f"Bearer {token}", session_id="sess-A")
    )
    assert messages[0]["status"] == 401
    assert seen["called"] is False
    assert "sess-A" not in middleware._bindings
