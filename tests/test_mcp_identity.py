"""`ClientRegistry.authenticate`: constant-time compare keyed by the presented
client id (spec 3.2; acceptance test 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from petasos.mcp import identity as identity_module
from petasos.mcp.identity import ClientRegistry
from petasos.sessions.store import SessionStore
from petasos.storage import Database


@pytest.fixture
def minted(tmp_sqlite: Path, frozen_clock):
    db = Database(tmp_sqlite)
    db.migrate()
    store = SessionStore(db, clock=frozen_clock)
    return db, store.mint()


def test_authenticate_succeeds_for_each_role(minted, frozen_clock) -> None:
    db, session = minted
    registry = ClientRegistry(db)
    for role, token in session.tokens.items():
        identity = registry.authenticate(token, now=frozen_clock())
        assert identity is not None
        assert identity.role == role
        assert identity.scope == session.session


def test_authenticate_fails_for_missing_id_and_wrong_secret(minted, frozen_clock) -> None:
    db, session = minted
    registry = ClientRegistry(db)

    assert registry.authenticate("no-such-id.secret", now=frozen_clock()) is None

    client_id = session.tokens["agent"].split(".", 1)[0]
    assert registry.authenticate(f"{client_id}.wrong-secret", now=frozen_clock()) is None


def test_authenticate_fails_for_a_secret_replayed_under_another_id(minted, frozen_clock) -> None:
    db, session = minted
    registry = ClientRegistry(db)
    agent_id = session.tokens["agent"].split(".", 1)[0]
    owner_secret = session.tokens["owner"].split(".", 1)[1]

    assert registry.authenticate(f"{agent_id}.{owner_secret}", now=frozen_clock()) is None


def test_authenticate_fails_once_the_identity_has_expired(minted, frozen_clock) -> None:
    db, session = minted
    registry = ClientRegistry(db)
    token = session.tokens["agent"]

    frozen_clock.advance(61 * 60)
    assert registry.authenticate(token, now=frozen_clock()) is None


def test_compare_digest_called_exactly_once_for_missing_and_present_ids(
    minted, frozen_clock, monkeypatch
) -> None:
    db, session = minted
    registry = ClientRegistry(db)
    calls = []
    original = identity_module.hmac.compare_digest

    def counting(a, b):
        calls.append(1)
        return original(a, b)

    monkeypatch.setattr(identity_module.hmac, "compare_digest", counting)

    calls.clear()
    registry.authenticate("no-such-id.secret", now=frozen_clock())
    assert len(calls) == 1

    client_id = session.tokens["agent"].split(".", 1)[0]
    calls.clear()
    registry.authenticate(f"{client_id}.wrong-secret", now=frozen_clock())
    assert len(calls) == 1
