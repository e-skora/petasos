"""`SessionStore.mint` and `.expire`: three distinct tokens, digests never the
secret, the one-hour life, and full namespace cleanup on expiry (spec 3.19, 3.20;
acceptance tests 21, 22, 24)."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from petasos.sessions import quotas
from petasos.sessions.store import MintCeilingExceeded, MintQuotaExceeded, SessionStore
from petasos.storage import Database


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


def test_mint_returns_three_distinct_tokens_and_digests_are_not_secrets(db, frozen_clock) -> None:
    store = SessionStore(db, clock=frozen_clock)
    minted = store.mint()

    assert set(minted.tokens) == {"visitor", "agent", "owner"}
    assert len({minted.tokens[role] for role in minted.tokens}) == 3
    assert minted.expires_at == frozen_clock() + timedelta(hours=1)

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, secret_digest FROM identities WHERE session_id=?", (minted.session,)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    for row in rows:
        role = row["id"].rsplit("-", 1)[-1]
        secret = minted.tokens[role].split(".", 1)[1]
        assert row["secret_digest"] != secret


def test_mint_seeds_six_visible_tickets(db, frozen_clock) -> None:
    store = SessionStore(db, clock=frozen_clock)
    a = store.mint()
    b = store.mint()

    conn = db.connect()
    try:
        count_a = conn.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE scope=? AND hidden=0", (a.session,)
        ).fetchone()["n"]
        count_b = conn.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE scope=? AND hidden=0", (b.session,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert count_a == 6
    assert count_b == 6


def test_expire_deletes_the_namespace_but_keeps_ledger_and_canaries(db, frozen_clock) -> None:
    store = SessionStore(db, clock=frozen_clock)
    minted = store.mint()

    frozen_clock.advance(61 * 60)
    removed = store.expire(frozen_clock())
    assert removed == 1

    conn = db.connect()
    try:
        scoped_tables = [
            "tickets",
            "notes",
            "helpdesk_fake_mail",
            "helpdesk_fake_refunds",
            "memory_entries",
            "grants",
            "rail_slots",
        ]
        for table in scoped_tables:
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE scope=?", (minted.session,)
            ).fetchone()["n"]
            assert count == 0, table
        identities = conn.execute(
            "SELECT COUNT(*) AS n FROM identities WHERE session_id=?", (minted.session,)
        ).fetchone()["n"]
        sessions = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE id=?", (minted.session,)
        ).fetchone()["n"]
        canaries = conn.execute("SELECT COUNT(*) AS n FROM canaries").fetchone()["n"]
    finally:
        conn.close()
    assert identities == 0
    assert sessions == 0
    assert canaries >= 1


def test_expire_runs_at_the_start_of_every_mint(db, frozen_clock) -> None:
    store = SessionStore(db, clock=frozen_clock)
    first = store.mint()

    frozen_clock.advance(61 * 60)
    store.mint()

    conn = db.connect()
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE id=?", (first.session,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert remaining == 0


def test_mint_over_hourly_quota_is_refused_and_writes_nothing(
    db, frozen_clock, monkeypatch
) -> None:
    monkeypatch.setattr(quotas, "MINTS_PER_HOUR", 2)
    store = SessionStore(db, clock=frozen_clock)
    store.mint()
    store.mint()

    with pytest.raises(MintQuotaExceeded):
        store.mint()

    conn = db.connect()
    try:
        sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    finally:
        conn.close()
    assert sessions == 2


def test_mint_over_daily_quota_is_refused_even_with_hourly_quota_free(
    db, frozen_clock, monkeypatch
) -> None:
    monkeypatch.setattr(quotas, "MINTS_PER_DAY", 2)
    store = SessionStore(db, clock=frozen_clock)
    store.mint()
    store.mint()

    with pytest.raises(MintQuotaExceeded):
        store.mint()


def test_mint_over_live_session_ceiling_is_refused(db, frozen_clock, monkeypatch) -> None:
    monkeypatch.setattr(quotas, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore(db, clock=frozen_clock)
    store.mint()
    store.mint()

    with pytest.raises(MintCeilingExceeded):
        store.mint()

    frozen_clock.advance(61 * 60)
    store.mint()  # expiry runs first, freeing a slot


def test_two_threads_minting_the_last_slot_only_one_succeeds(db, frozen_clock, monkeypatch) -> None:
    monkeypatch.setattr(quotas, "MAX_LIVE_SESSIONS", 3)
    store = SessionStore(db, clock=frozen_clock)
    store.mint()
    store.mint()

    outcomes: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            store.mint()
            ok = True
        except MintCeilingExceeded:
            ok = False
        with lock:
            outcomes.append(ok)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count(True) == 1
