"""The guard seam: unit buffering (JSON and SSE), the byte limits, canary and
private-key hits, route labels, and the fail-closed ledger write (spec 3.9, 3.10;
acceptance tests 15 to 20)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import petasos.mcp.guard_seam as guard_seam_module
from petasos.mcp.guard_seam import (
    MAX_BODY_BYTES,
    MAX_EVENT_BYTES,
    UNGUARDED_ROUTES,
    GuardSeam,
    _route_label,
)
from petasos.memory.store import MemoryStore, MemoryTier
from petasos.sessions.quotas import counter_value
from petasos.storage import Database


def _scope(path: str = "/mcp") -> dict:
    return {"type": "http", "path": path, "headers": [], "state": {}}


async def _no_receive() -> dict:
    return {"type": "http.disconnect"}


def _make_app(messages: list[dict]):
    async def app(scope, receive, send) -> None:
        for message in messages:
            await send(message)

    return app


async def _run(app, db: Database, clock, scope: dict) -> list[dict]:
    seam = GuardSeam(app, db, clock)
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    await seam(scope, _no_receive, send)
    return sent


def _json_messages(body: bytes, *, chunk_size: int | None = None) -> list[dict]:
    start = {
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"application/json")],
    }
    if chunk_size is None:
        return [start, {"type": "http.response.body", "body": body, "more_body": False}]
    messages = [start]
    for i in range(0, len(body), chunk_size):
        chunk = body[i : i + chunk_size]
        more = (i + chunk_size) < len(body)
        messages.append({"type": "http.response.body", "body": chunk, "more_body": more})
    if not body:
        messages.append({"type": "http.response.body", "body": b"", "more_body": False})
    return messages


def _sse_messages(raw: bytes, *, chunk_size: int | None = None) -> list[dict]:
    start = {
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/event-stream")],
    }
    if chunk_size is None:
        return [start, {"type": "http.response.body", "body": raw, "more_body": False}]
    messages = [start]
    for i in range(0, len(raw), chunk_size):
        chunk = raw[i : i + chunk_size]
        more = (i + chunk_size) < len(raw)
        messages.append({"type": "http.response.body", "body": chunk, "more_body": more})
    if not raw:
        messages.append({"type": "http.response.body", "body": b"", "more_body": False})
    return messages


def _mint_canary(db: Database, clock, scope: str = "s1") -> str:
    store = MemoryStore(db, scope=scope, clock=clock)
    entry = store.put("secret", "hello there", MemoryTier.T4)
    assert entry.canary is not None
    return entry.canary


def _ledger_rows(db: Database) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
    finally:
        conn.close()
    return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


async def test_healthz_bypasses_the_seam_even_with_a_broken_guard(
    db, frozen_clock, monkeypatch
) -> None:
    monkeypatch.setattr(
        guard_seam_module, "guard_scan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    app = _make_app(_json_messages(b'{"ok": true}'))
    sent = await _run(app, db, frozen_clock, _scope("/healthz"))
    assert sent[0]["status"] == 200
    assert sent[-1]["body"] == b'{"ok": true}'


async def test_plain_json_body_passes_through_unchanged(db, frozen_clock) -> None:
    body = b'{"hello": "world"}'
    app = _make_app(_json_messages(body))
    sent = await _run(app, db, frozen_clock, _scope("/session"))
    assert sent[0]["status"] == 200
    assert sent[-1]["body"] == body


async def test_json_body_at_exactly_the_byte_limit_passes(db, frozen_clock) -> None:
    body = b'"' + b"a" * (MAX_BODY_BYTES - 2) + b'"'
    assert len(body) == MAX_BODY_BYTES
    app = _make_app(_json_messages(body, chunk_size=65536))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 200


async def test_json_body_over_the_byte_limit_is_a_guard_error(db, frozen_clock) -> None:
    body = b"a" * (MAX_BODY_BYTES + 1)
    app = _make_app(_json_messages(body, chunk_size=65536))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409
    payload = json.loads(sent[-1]["body"])
    assert payload["error"]["data"]["status"] == "refused/guard_tripped"


async def test_json_body_with_a_canary_trips_the_guard_and_ledgers_a_row(db, frozen_clock) -> None:
    token = _mint_canary(db, frozen_clock, scope="s1")
    body = json.dumps({"data": f"leaked {token}"}).encode()
    app = _make_app(_json_messages(body))
    scope = _scope("/mcp")
    scope["state"]["identity"] = SimpleNamespace(id="s1-agent", scope="s1")

    sent = await _run(app, db, frozen_clock, scope)
    assert sent[0]["status"] == 409
    payload_text = json.dumps(json.loads(sent[-1]["body"]))
    assert token not in payload_text

    rows = _ledger_rows(db)
    assert rows[-1]["detail"]["code"] == "guard/canary"
    assert rows[-1]["detail"]["route"] == "mcp"
    assert rows[-1]["actor"] == "s1-agent"
    assert rows[-1]["scope"] == "s1"

    conn = db.connect()
    try:
        assert counter_value(conn, "guard_trips") == 1
    finally:
        conn.close()


async def test_private_key_nested_inside_a_json_string_is_caught(db, frozen_clock) -> None:
    inner = json.dumps({"private_notes": "secret"})
    body = json.dumps({"content": [{"type": "text", "text": inner}]}).encode()
    app = _make_app(_json_messages(body))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409

    rows = _ledger_rows(db)
    assert rows[-1]["detail"]["code"] == "guard/private_key"


async def test_sse_comment_only_event_passes(db, frozen_clock) -> None:
    raw = b": ping\r\n\r\n"
    app = _make_app(_sse_messages(raw))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 200
    body_bytes = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert body_bytes == raw


async def test_sse_first_event_hit_is_a_409(db, frozen_clock) -> None:
    token = _mint_canary(db, frozen_clock, scope="s1")
    raw = f'data: {{"leak": "{token}"}}\r\n\r\n'.encode()
    app = _make_app(_sse_messages(raw))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409
    assert sent[0]["headers"] == [(b"content-type", b"application/json")]
    joined = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert token.encode() not in joined


async def test_sse_canary_in_id_line_still_trips_the_guard(db, frozen_clock) -> None:
    token = _mint_canary(db, frozen_clock, scope="s1")
    raw = f'id: {token}\r\ndata: {{"ok": true}}\r\n\r\n'.encode()
    app = _make_app(_sse_messages(raw))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409
    joined = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert token.encode() not in joined


async def test_sse_later_event_hit_terminates_with_event_message(db, frozen_clock) -> None:
    token = _mint_canary(db, frozen_clock, scope="s1")
    clean_event = b'event: message\r\ndata: {"ok": true}\r\n\r\n'
    hit_event = f'event: message\r\ndata: {{"leak": "{token}"}}\r\n\r\n'.encode()
    raw = clean_event + hit_event
    app = _make_app(_sse_messages(raw))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))

    assert sent[0]["status"] == 200
    bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
    assert bodies[0] == clean_event
    joined = b"".join(bodies)
    assert token.encode() not in joined
    assert b"event: message" in bodies[-1]
    assert sent[-1]["more_body"] is False


@pytest.mark.parametrize("split", range(26))
async def test_sse_canary_split_across_chunks_is_still_caught(db, frozen_clock, split) -> None:
    token = _mint_canary(db, frozen_clock, scope="s1")
    raw = f'data: {{"leak": "{token}"}}\r\n\r\n'.encode()
    split = min(split, len(raw))
    messages = [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        },
        {"type": "http.response.body", "body": raw[:split], "more_body": True},
        {"type": "http.response.body", "body": raw[split:], "more_body": False},
    ]
    app = _make_app(messages)
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409
    joined = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert token.encode() not in joined


async def test_sse_event_at_exactly_the_byte_limit_passes(db, frozen_clock) -> None:
    prefix, suffix = b"data: ", b"\r\n\r\n"
    raw = prefix + b"a" * (MAX_EVENT_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(raw) == MAX_EVENT_BYTES
    app = _make_app(_sse_messages(raw, chunk_size=65536))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 200


async def test_sse_event_over_the_byte_limit_is_a_guard_error(db, frozen_clock) -> None:
    prefix, suffix = b"data: ", b"\r\n\r\n"
    raw = prefix + b"a" * (MAX_EVENT_BYTES - len(prefix) - len(suffix) + 1) + suffix
    app = _make_app(_sse_messages(raw, chunk_size=65536))
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409


async def test_sse_event_that_never_terminates_within_the_limit_is_a_guard_error(
    db, frozen_clock
) -> None:
    raw = b"data: " + b"a" * (MAX_EVENT_BYTES + 10)
    messages = [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        },
        {"type": "http.response.body", "body": raw, "more_body": False},
    ]
    app = _make_app(messages)
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409


async def test_sse_nonempty_incomplete_trailing_event_at_end_is_a_guard_error(
    db, frozen_clock
) -> None:
    raw = b"data: incomplete"
    messages = [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        },
        {"type": "http.response.body", "body": raw, "more_body": False},
    ]
    app = _make_app(messages)
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 409


async def test_sse_stream_with_no_unit_releases_start_and_completes(db, frozen_clock) -> None:
    messages = [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        },
        {"type": "http.response.body", "body": b"", "more_body": False},
    ]
    app = _make_app(messages)
    sent = await _run(app, db, frozen_clock, _scope("/mcp"))
    assert sent[0]["status"] == 200
    assert sent[-1]["body"] == b""
    assert sent[-1]["more_body"] is False


async def test_scan_raising_makes_every_response_a_guard_error(
    db, frozen_clock, monkeypatch
) -> None:
    def _raiser(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(guard_seam_module, "guard_scan", _raiser)

    marker = "synthetic-marker-xyz"
    body = json.dumps({"ok": True}).encode()
    app = _make_app(_json_messages(body))
    scope = _scope(f"/no-such-route?x={marker}")

    sent = await _run(app, db, frozen_clock, scope)
    assert sent[0]["status"] == 409

    rows = _ledger_rows(db)
    assert rows[-1]["detail"]["code"] == "guard/error"
    assert rows[-1]["detail"]["route"] == "unmatched"
    assert rows[-1]["actor"] == "anonymous"
    for row in rows:
        assert marker not in json.dumps(row)


async def test_ledger_write_failure_still_terminates_and_logs_once_with_no_payload(
    db, frozen_clock, monkeypatch, caplog
) -> None:
    token = _mint_canary(db, frozen_clock, scope="s1")
    body = json.dumps({"leak": token}).encode()
    app = _make_app(_json_messages(body))

    def boom(*args, **kwargs):
        raise RuntimeError("ledger boom")

    monkeypatch.setattr(db, "transaction", boom)

    with caplog.at_level(logging.ERROR):
        sent = await _run(app, db, frozen_clock, _scope("/mcp"))

    assert sent[0]["status"] == 409
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_logs) == 1
    assert token not in error_logs[0].getMessage()


def test_route_label_matches_known_paths() -> None:
    assert _route_label("/mcp") == "mcp"
    assert _route_label("/mcp/") == "mcp"
    assert _route_label("/session") == "session"
    assert _route_label("/healthz") == "healthz"
    assert _route_label("/nope") == "unmatched"


def test_only_healthz_is_unguarded_among_the_apps_routes(db, frozen_clock) -> None:
    from petasos.app import create_app

    app = create_app(db, clock=frozen_clock)
    paths = {getattr(route, "path", None) for route in app.routes}
    paths.discard(None)
    assert UNGUARDED_ROUTES == {"/healthz"}
    assert "/healthz" in paths
    assert paths - {"/healthz"}
    for path in paths - {"/healthz"}:
        assert path not in UNGUARDED_ROUTES
