"""Acceptance test 3 (spec 005 5.4): the request size bound, outermost in the
process. Hand-built ASGI scopes against a recording app, then the full app through
`TestClient`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient
from support_005 import RecordingApp, call_asgi, http_scope

from petasos.limits import MAX_REQUEST_BYTES, RequestSizeLimit
from petasos.serve import Settings, build_app


@pytest.fixture
def recording() -> RecordingApp:
    return RecordingApp()


@pytest.fixture
def limited(recording: RecordingApp) -> RequestSizeLimit:
    return RequestSizeLimit(recording)


async def test_content_length_at_the_cap_passes(limited, recording) -> None:
    scope = http_scope(method="POST", path="/session", headers={"content-length": "65536"})
    sent = await call_asgi(limited, scope)
    assert recording.calls == [scope]
    assert sent[0]["status"] == 200


async def test_content_length_over_the_cap_is_413(limited, recording) -> None:
    scope = http_scope(method="POST", path="/session", headers={"content-length": "65537"})
    sent = await call_asgi(limited, scope)
    assert recording.calls == []
    assert sent[0]["status"] == 413
    assert sent[0]["headers"] == [(b"content-type", b"application/json")]
    assert b"refused/too_large" in sent[1]["body"]


@pytest.mark.parametrize("value", ["abc", "-1"])
async def test_non_digit_content_length_is_413(limited, recording, value: str) -> None:
    scope = http_scope(method="POST", path="/session", headers={"content-length": value})
    sent = await call_asgi(limited, scope)
    assert recording.calls == []
    assert sent[0]["status"] == 413


async def test_chunked_with_no_content_length_is_411(limited, recording) -> None:
    scope = http_scope(method="POST", path="/session", headers={"transfer-encoding": "chunked"})
    sent = await call_asgi(limited, scope)
    assert recording.calls == []
    assert sent[0]["status"] == 411
    assert b"refused/length_required" in sent[1]["body"]


async def test_chunked_with_content_length_is_still_411(limited, recording) -> None:
    scope = http_scope(
        method="POST",
        path="/session",
        headers={"transfer-encoding": "chunked", "content-length": "5"},
    )
    sent = await call_asgi(limited, scope)
    assert recording.calls == []
    assert sent[0]["status"] == 411


async def test_post_with_neither_header_passes(limited, recording) -> None:
    scope = http_scope(method="POST", path="/session")
    sent = await call_asgi(limited, scope)
    assert recording.calls == [scope]
    assert sent[0]["status"] == 200


async def test_get_with_neither_header_passes(limited, recording) -> None:
    scope = http_scope(method="GET", path="/healthz")
    await call_asgi(limited, scope)
    assert recording.calls == [scope]


async def test_lifespan_scope_passes_through(limited, recording) -> None:
    scope = {"type": "lifespan"}

    async def receive():
        return {"type": "lifespan.startup"}

    sent = []

    async def send(message):
        sent.append(message)

    await limited(scope, receive, send)
    assert recording.calls == [scope]


def test_both_refusal_bodies_have_no_code_id_or_underscore_beyond_the_status() -> None:
    import json

    from petasos.limits import _LENGTH_REQUIRED_PAYLOAD, _TOO_LARGE_PAYLOAD

    for payload in (_LENGTH_REQUIRED_PAYLOAD, _TOO_LARGE_PAYLOAD):
        assert payload["tier"] is None
        assert payload["verb"] is None
        assert payload["grant_id"] is None
        json.dumps(payload)


def test_too_large_sentence_matches_owners_when_that_module_imports() -> None:
    try:
        from petasos.owner.outcomes import OWNER_SENTENCES
    except ImportError:
        pytest.skip("change 004 has not merged yet")
    from petasos.limits import _TOO_LARGE_PAYLOAD

    assert _TOO_LARGE_PAYLOAD["explanation"] == OWNER_SENTENCES["refused/too_large"]


class _App:
    def __init__(self) -> None:
        self.app = None


async def test_app_attribute_holds_the_wrapped_app(recording: RecordingApp) -> None:
    limited = RequestSizeLimit(recording)
    assert limited.app is recording


def test_full_app_generator_body_is_411(tmp_path: Path, frozen_clock) -> None:
    settings = Settings(
        db_path=tmp_path / "p.sqlite", archive_dir=tmp_path / "archive", host="0.0.0.0", port=8080
    )
    app = build_app(settings, clock=frozen_clock)
    with TestClient(app) as client:

        def body():
            yield b"{}"

        response = client.post("/session", content=body())
    assert response.status_code == 411


def test_full_app_oversized_body_is_413(tmp_path: Path, frozen_clock) -> None:
    settings = Settings(
        db_path=tmp_path / "p.sqlite", archive_dir=tmp_path / "archive", host="0.0.0.0", port=8080
    )
    app = build_app(settings, clock=frozen_clock)
    with TestClient(app) as client:
        response = client.post("/session", content=b"x" * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413


def test_full_app_bodiless_post_session_still_mints(tmp_path: Path, frozen_clock) -> None:
    settings = Settings(
        db_path=tmp_path / "p.sqlite", archive_dir=tmp_path / "archive", host="0.0.0.0", port=8080
    )
    app = build_app(settings, clock=frozen_clock)
    with TestClient(app) as client:
        response = client.post("/session")
    assert response.status_code == 201


def test_full_app_unauthenticated_oversized_mcp_post_is_413_with_no_token_or_id(
    tmp_path: Path, frozen_clock
) -> None:
    settings = Settings(
        db_path=tmp_path / "p.sqlite", archive_dir=tmp_path / "archive", host="0.0.0.0", port=8080
    )
    app = build_app(settings, clock=frozen_clock)
    with TestClient(app) as client:
        response = client.post("/mcp", content=b"x" * 70_000)
    assert response.status_code == 413
    assert b"token" not in response.content
    assert b'grant_id":null' in response.content or b'"grant_id": null' in response.content
