"""MemoryStore: tiers below T4 store text unchanged, input validation, expiry, and
scope isolation (spec 002-memory-canary-guard 2.1 to 2.4, 2.8 to 2.10; acceptance
tests 3, 8, 13 to 16).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from petasos.memory import InvalidEntry, MemoryStore, MemoryTier
from petasos.storage import Database


def _db(tmp_sqlite: Path) -> Database:
    db = Database(tmp_sqlite)
    db.migrate()
    return db


def _count(db: Database, table: str) -> int:
    with db.transaction() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def test_put_below_t4_stores_text_unchanged_with_no_canary(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    for tier in (MemoryTier.T0, MemoryTier.T1, MemoryTier.T2, MemoryTier.T3):
        entry = store.put(f"key-{tier.value}", "hello there", tier)
        assert entry.canary is None
        assert entry.text == "hello there"
    assert _count(db, "canaries") == 0


def test_put_rejects_empty_key(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with pytest.raises(InvalidEntry):
        store.put("", "hi", MemoryTier.T1)
    assert _count(db, "memory_entries") == 0


def test_put_rejects_empty_text(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with pytest.raises(InvalidEntry):
        store.put("k", "", MemoryTier.T1)
    assert _count(db, "memory_entries") == 0


def test_put_rejects_a_key_over_128_characters(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with pytest.raises(InvalidEntry):
        store.put("x" * 129, "hi", MemoryTier.T1)
    assert _count(db, "memory_entries") == 0


def test_put_rejects_text_over_8192_characters(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with pytest.raises(InvalidEntry):
        store.put("k", "x" * 8193, MemoryTier.T1)
    assert _count(db, "memory_entries") == 0


def test_put_rejects_a_naive_expires_at(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    naive = frozen_clock().replace(tzinfo=None) + timedelta(seconds=1)
    with pytest.raises(InvalidEntry):
        store.put("k", "hi", MemoryTier.T1, expires_at=naive)
    assert _count(db, "memory_entries") == 0


def test_put_rejects_expires_at_equal_to_clock(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with pytest.raises(InvalidEntry):
        store.put("k", "hi", MemoryTier.T1, expires_at=frozen_clock())
    assert _count(db, "memory_entries") == 0


@pytest.mark.parametrize("limit", [0, 101])
def test_search_rejects_out_of_range_limit(tmp_sqlite: Path, frozen_clock, limit) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with pytest.raises(InvalidEntry):
        store.search("", ceiling=MemoryTier.T1, limit=limit)


def test_put_in_rolls_back_with_the_callers_transaction(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)

    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.transaction() as conn:
        store.put_in(conn, "k", "hello", MemoryTier.T1)
        raise Boom()

    assert _count(db, "memory_entries") == 0


def test_put_in_commits_with_the_callers_transaction(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    with db.transaction() as conn:
        store.put_in(conn, "k", "hello", MemoryTier.T1)

    assert store.read("k", ceiling=MemoryTier.T1) is not None


def test_search_by_ceiling_returns_entries_at_or_below(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    for tier in MemoryTier:
        store.put(f"key-{tier.value}", f"text {tier.value}", tier)

    for ceiling in MemoryTier:
        results = {e.key for e in store.search("", ceiling=ceiling)}
        expected = {f"key-{t.value}" for t in MemoryTier if t <= ceiling}
        assert results == expected

    assert store.read("key-T5", ceiling=MemoryTier.T4) is None
    assert store.read("key-T5", ceiling=MemoryTier.T5) is not None


def test_search_is_case_insensitive_newest_first_and_honours_limit(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    store.put("Alpha", "First TICKET note", MemoryTier.T1)
    frozen_clock.advance(1)
    store.put("Beta", "second ticket NOTE", MemoryTier.T1)
    frozen_clock.advance(1)
    store.put("Gamma", "unrelated", MemoryTier.T1)

    results = store.search("ticket", ceiling=MemoryTier.T1)
    assert [e.key for e in results] == ["Beta", "Alpha"]

    limited = store.search("", ceiling=MemoryTier.T1, limit=1)
    assert len(limited) == 1
    assert limited[0].key == "Gamma"


def test_search_never_returns_entries_from_another_scope(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store1 = MemoryStore(db, scope="s1", clock=frozen_clock)
    store2 = MemoryStore(db, scope="s2", clock=frozen_clock)
    store1.put("shared-key", "s1 text", MemoryTier.T1)
    store2.put("shared-key", "s2 text", MemoryTier.T1)

    assert store1.read("shared-key", ceiling=MemoryTier.T1).text == "s1 text"
    assert store2.read("shared-key", ceiling=MemoryTier.T1).text == "s2 text"
    assert [e.key for e in store1.search("", ceiling=MemoryTier.T5)] == ["shared-key"]


def test_sweep_in_one_scope_deletes_nothing_of_another(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store1 = MemoryStore(db, scope="s1", clock=frozen_clock)
    store2 = MemoryStore(db, scope="s2", clock=frozen_clock)
    store1.put("k1", "text", MemoryTier.T1, expires_at=frozen_clock() + timedelta(seconds=1))
    store2.put("k2", "text", MemoryTier.T1)

    frozen_clock.advance(1)
    store2.sweep()

    with db.transaction() as conn:
        remaining = {row["key"] for row in conn.execute("SELECT key FROM memory_entries")}
    assert remaining == {"k1", "k2"}


def test_expiry_boundary_hides_the_entry_from_read_and_search(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    expires_at = frozen_clock() + timedelta(seconds=1)
    store.put("k", "text", MemoryTier.T1, expires_at=expires_at)

    assert store.read("k", ceiling=MemoryTier.T1) is not None

    frozen_clock.advance(1)
    assert store.read("k", ceiling=MemoryTier.T1) is None
    assert store.search("text", ceiling=MemoryTier.T1) == []
    assert _count(db, "memory_entries") == 0


def test_read_deletes_an_expired_row(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    store.put("k", "text", MemoryTier.T1, expires_at=frozen_clock() + timedelta(seconds=1))
    frozen_clock.advance(1)
    store.read("k", ceiling=MemoryTier.T1)
    assert _count(db, "memory_entries") == 0


def test_search_deletes_an_expired_row(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    store.put("k", "text", MemoryTier.T1, expires_at=frozen_clock() + timedelta(seconds=1))
    frozen_clock.advance(1)
    store.search("", ceiling=MemoryTier.T1)
    assert _count(db, "memory_entries") == 0


def test_sweep_deletes_an_expired_row(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    store.put("k", "text", MemoryTier.T1, expires_at=frozen_clock() + timedelta(seconds=1))
    frozen_clock.advance(1)
    store.sweep()
    assert _count(db, "memory_entries") == 0


def test_put_of_another_key_sweeps_the_expired_row_first(tmp_sqlite: Path, frozen_clock) -> None:
    db = _db(tmp_sqlite)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)
    store.put("k", "text", MemoryTier.T1, expires_at=frozen_clock() + timedelta(seconds=1))
    frozen_clock.advance(1)
    store.put("other", "text", MemoryTier.T1)
    assert _count(db, "memory_entries") == 1
