"""`seed_session`: six visible tickets, one note, the hidden canary ticket, and five
memory entries across three tiers (spec 3.13)."""

from __future__ import annotations

from pathlib import Path

from petasos.helpdesk.data import CANARY_TICKET_NUMBER, seed_session
from petasos.memory.store import MemoryStore, MemoryTier
from petasos.storage import Database


def _seeded(tmp_sqlite: Path, clock, scope: str = "s1") -> Database:
    db = Database(tmp_sqlite)
    db.migrate()
    with db.transaction() as conn:
        seed_session(conn, db, scope, clock)
    return db


def test_seed_creates_six_visible_tickets_and_one_hidden(tmp_sqlite: Path, frozen_clock) -> None:
    db = _seeded(tmp_sqlite, frozen_clock)
    conn = db.connect()
    try:
        visible = conn.execute(
            "SELECT number FROM tickets WHERE scope=? AND hidden=0 ORDER BY number", ("s1",)
        ).fetchall()
        hidden = conn.execute(
            "SELECT number, hidden, deleted FROM tickets WHERE scope=? AND hidden=1", ("s1",)
        ).fetchone()
    finally:
        conn.close()
    assert [row["number"] for row in visible] == [1, 2, 3, 4, 5, 6]
    assert hidden["number"] == CANARY_TICKET_NUMBER == 7
    assert hidden["deleted"] == 0


def test_ticket_three_is_the_open_refund_ticket(tmp_sqlite: Path, frozen_clock) -> None:
    db = _seeded(tmp_sqlite, frozen_clock)
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tickets WHERE scope=? AND number=3", ("s1",)).fetchone()
    finally:
        conn.close()
    assert row["amount_minor"] == 4200
    assert row["currency"] == "USD"
    assert row["status"] == "open"


def test_ticket_five_is_resolved(tmp_sqlite: Path, frozen_clock) -> None:
    db = _seeded(tmp_sqlite, frozen_clock)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM tickets WHERE scope=? AND number=5", ("s1",)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "resolved"


def test_ticket_two_carries_one_note(tmp_sqlite: Path, frozen_clock) -> None:
    db = _seeded(tmp_sqlite, frozen_clock)
    conn = db.connect()
    try:
        ticket = conn.execute(
            "SELECT id FROM tickets WHERE scope=? AND number=2", ("s1",)
        ).fetchone()
        notes = conn.execute("SELECT * FROM notes WHERE ticket_id=?", (ticket["id"],)).fetchall()
    finally:
        conn.close()
    assert len(notes) == 1


def test_canary_ticket_body_carries_a_canary(tmp_sqlite: Path, frozen_clock) -> None:
    db = _seeded(tmp_sqlite, frozen_clock)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT body FROM tickets WHERE scope=? AND number=7", ("s1",)
        ).fetchone()
    finally:
        conn.close()
    assert "cn-" in row["body"]


def test_two_sessions_seed_different_text_in_every_field(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    with db.transaction() as conn:
        seed_session(conn, db, "s1", frozen_clock)
        seed_session(conn, db, "s2", frozen_clock)
    conn = db.connect()
    try:
        a = conn.execute("SELECT * FROM tickets WHERE scope='s1' AND number=3").fetchone()
        b = conn.execute("SELECT * FROM tickets WHERE scope='s2' AND number=3").fetchone()
    finally:
        conn.close()
    assert a["customer"] != b["customer"]
    assert a["email"] != b["email"]
    assert a["body"] != b["body"]


def test_recall_returns_three_different_sets_by_ceiling(tmp_sqlite: Path, frozen_clock) -> None:
    db = _seeded(tmp_sqlite, frozen_clock)
    store = MemoryStore(db, scope="s1", clock=frozen_clock)

    at_t0 = store.search("", ceiling=MemoryTier.T0)
    at_t3 = store.search("", ceiling=MemoryTier.T3)
    at_t4 = store.search("", ceiling=MemoryTier.T4)

    assert len(at_t0) == 1
    assert len(at_t3) == 3
    assert len(at_t4) == 5
    assert sum(1 for e in at_t4 if e.tier == MemoryTier.T4) == 2
