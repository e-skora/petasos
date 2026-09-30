"""The reader test: plant a canary, hand the guard a payload containing it, and watch
the guard abort, with no MCP server or demo app needed (spec 002-memory-canary-guard,
acceptance test 25).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from petasos.memory import (
    GuardTripped,
    MemoryStore,
    MemoryTier,
    all_canaries,
    assert_no_private_payload,
    canary_entry,
)
from petasos.storage import Database


def test_planting_a_canary_and_leaking_it_trips_the_guard(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    store = MemoryStore(db, scope="visitor-1", clock=frozen_clock)
    store.put("ticket-secret", "Card ending 4242 was charged twice.", MemoryTier.T4)

    entry = store.read("ticket-secret", ceiling=MemoryTier.T4)
    payload = {"reply": f"Here is what we found: {entry.text}"}

    with db.transaction() as conn:
        canaries = all_canaries(conn)

    with pytest.raises(GuardTripped) as excinfo:
        assert_no_private_payload(payload, canaries)

    hit = excinfo.value.hit
    assert hit.kind == "canary"

    with db.transaction() as conn:
        looked_up = canary_entry(conn, hit.token)
    assert looked_up == (entry.id, "visitor-1")


def test_a_ceiling_below_the_entrys_tier_leaves_nothing_to_leak(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    store = MemoryStore(db, scope="visitor-1", clock=frozen_clock)
    store.put("ticket-secret", "Card ending 4242 was charged twice.", MemoryTier.T4)

    entry = store.read("ticket-secret", ceiling=MemoryTier.T3)
    assert entry is None
