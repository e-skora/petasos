"""Canary minting, replanting, and the one-canary-for-life rule (spec
002-memory-canary-guard 2.5 to 2.7, 2.14; acceptance tests 4 to 12).
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

from petasos.memory import (
    CanarySet,
    MemoryStore,
    MemoryTier,
    TierLocked,
    all_canaries,
    canary_entry,
    mint_canary,
    scan,
)
from petasos.memory.canary import marker_for
from petasos.storage import Database

_TOKEN_RE = re.compile(r"^cn-[A-Za-z0-9_-]{22}$")


def _db(tmp_sqlite: Path) -> Database:
    db = Database(tmp_sqlite)
    db.migrate()
    return db


def _canary_rows(db: Database) -> list[dict]:
    with db.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM canaries").fetchall()]


@pytest.mark.parametrize("tier", [MemoryTier.T4, MemoryTier.T5])
def test_put_at_t4_or_above_mints_exactly_one_canary(tmp_sqlite: Path, frozen_clock, tier) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    entry = store.put("secret", "the card was charged twice", tier)

    assert entry.canary is not None
    assert _TOKEN_RE.match(entry.canary)
    assert entry.text == f"the card was charged twice{marker_for(entry.canary)}"
    assert entry.text.count(entry.canary) == 1

    rows = _canary_rows(db)
    assert len(rows) == 1
    assert rows[0]["token"] == entry.canary
    assert rows[0]["entry_id"] == entry.id


def test_repeated_put_at_t4_keeps_one_canary_and_replants_it(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    first = store.put("ticket-7.private", "Card ending 4242 was charged twice.", MemoryTier.T4)
    second = store.put("ticket-7.private", "Refund initiated.", MemoryTier.T4)
    third = store.put("ticket-7.private", "Refund completed.", MemoryTier.T4)

    assert first.canary == second.canary == third.canary
    assert third.text == f"Refund completed.{marker_for(third.canary)}"
    assert len(_canary_rows(db)) == 1


def test_raising_a_key_to_t4_mints_a_canary_only_once(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    low = store.put("k", "low tier text", MemoryTier.T2)
    assert low.canary is None

    raised = store.put("k", "now private", MemoryTier.T4)
    assert raised.canary is not None

    raised_again = store.put("k", "still private", MemoryTier.T5)
    assert raised_again.canary == raised.canary
    assert len(_canary_rows(db)) == 1


def test_lowering_a_key_with_a_canary_raises_tier_locked(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    entry = store.put("k", "private text", MemoryTier.T4)

    with pytest.raises(TierLocked):
        store.put("k", "trying to downgrade", MemoryTier.T3)

    unchanged = store.read("k", ceiling=MemoryTier.T4)
    assert unchanged.text == entry.text
    assert unchanged.tier == MemoryTier.T4
    assert len(_canary_rows(db)) == 1


def test_put_in_writes_entry_and_canary_atomically(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)

    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.transaction() as conn:
        store.put_in(conn, "k", "private text", MemoryTier.T4)
        raise Boom()

    with db.transaction() as conn:
        entries = conn.execute("SELECT COUNT(*) AS n FROM memory_entries").fetchone()["n"]
        canaries = conn.execute("SELECT COUNT(*) AS n FROM canaries").fetchone()["n"]
    assert entries == 0
    assert canaries == 0

    with db.transaction() as conn:
        entry = store.put_in(conn, "k", "private text", MemoryTier.T4)
    assert store.read("k", ceiling=MemoryTier.T4).canary == entry.canary
    assert len(_canary_rows(db)) == 1


@pytest.mark.parametrize("new_tier", [MemoryTier.T0, MemoryTier.T4])
def test_overwriting_an_expired_t4_entry_gets_a_new_id_and_keeps_the_old_token_live(
    tmp_sqlite: Path, frozen_clock, new_tier
) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    expires_at = frozen_clock() + timedelta(seconds=1)
    original = store.put("k", "will expire", MemoryTier.T4, expires_at=expires_at)
    frozen_clock.advance(1)

    replacement = store.put("k", "fresh text", new_tier)

    assert replacement.id != original.id
    assert replacement.tier == new_tier
    if new_tier == MemoryTier.T4:
        assert replacement.canary is not None
        assert replacement.canary != original.canary
    else:
        assert replacement.canary is None

    with db.transaction() as conn:
        registry = all_canaries(conn)
        looked_up = canary_entry(conn, original.canary)
    assert original.canary in registry
    assert scan({"leak": original.canary}, registry) is not None
    assert looked_up == (original.id, "s1")


def test_overwriting_an_expired_t4_entry_via_put_in_rolls_back_together(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    expires_at = frozen_clock() + timedelta(seconds=1)
    original = store.put("k", "will expire", MemoryTier.T4, expires_at=expires_at)
    frozen_clock.advance(1)

    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.transaction() as conn:
        store.put_in(conn, "k", "fresh text", MemoryTier.T4)
        conn.execute(
            "INSERT INTO memory_entries "
            "(scope, key, tier, text, created_at, updated_at, expires_at, canary_id) "
            "VALUES ('s1', 'other', 'T1', 'x', 'x', 'x', NULL, NULL)"
        )
        raise Boom()

    with db.transaction() as conn:
        rows = conn.execute("SELECT key FROM memory_entries").fetchall()
    assert [row["key"] for row in rows] == ["k"]
    with db.transaction() as conn:
        registry = all_canaries(conn)
    assert original.canary in registry


def test_reading_a_t4_entry_and_putting_its_text_back_stores_one_marker(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    original = store.put("k", "x" * 8192, MemoryTier.T4)
    assert len(original.text) == 8192 + 32

    read_back = store.read("k", ceiling=MemoryTier.T4)
    put_back = store.put("k", read_back.text, MemoryTier.T4)

    assert put_back.canary == original.canary
    assert put_back.text == original.text
    assert len(put_back.text) == 8224
    assert put_back.text.count(marker_for(original.canary)) == 1


def test_a_swept_t4_entrys_canary_outlives_it(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    entry = store.put(
        "k", "private text", MemoryTier.T4, expires_at=frozen_clock() + timedelta(seconds=1)
    )

    frozen_clock.advance(1)
    store.sweep()

    with db.transaction() as conn:
        entries_left = conn.execute("SELECT COUNT(*) AS n FROM memory_entries").fetchone()["n"]
        registry = all_canaries(conn)
    assert entries_left == 0
    assert entry.canary in registry
    assert scan({"leak": entry.canary}, registry) is not None


def test_mint_canary_produces_distinct_well_formed_tokens() -> None:
    tokens = {mint_canary() for _ in range(10_000)}
    assert len(tokens) == 10_000
    assert all(_TOKEN_RE.match(t) for t in tokens)


def test_all_canaries_spans_every_scope(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store1 = MemoryStore(db, scope="s1", clock=frozen_clock)
    store2 = MemoryStore(db, scope="s2", clock=frozen_clock)
    entry2 = store2.put("secret", "private text", MemoryTier.T4)

    store1.put("unrelated", "nothing private here", MemoryTier.T1)
    with db.transaction() as conn:
        registry_seen_from_s1 = all_canaries(conn)
    assert isinstance(registry_seen_from_s1, CanarySet)
    assert entry2.canary in registry_seen_from_s1
