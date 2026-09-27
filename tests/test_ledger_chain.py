"""append/verify: editing a row is detected, and deleting the genesis row is reported
rather than silently accepted as a new genesis (spec 1.22, 1.23; acceptance tests 34, 35).
"""

from __future__ import annotations

from pathlib import Path

from petasos.ledger import Ledger, append
from petasos.storage import Database


def _append_rows(db: Database, clock, n: int) -> None:
    for i in range(n):
        with db.transaction() as conn:
            append(
                conn,
                now=clock(),
                kind="staged",
                scope="s1",
                actor="agent",
                verb="SEND-EMAIL",
                tier="L4",
                grant_id=i + 1,
                record_hash=f"hash-{i}",
                detail={"code": "staged"},
            )
        clock.advance(1)


def test_untouched_ledger_verifies_clean(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    _append_rows(db, frozen_clock, 5)
    assert Ledger(db).verify() is None


def test_editing_a_row_is_detected(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    _append_rows(db, frozen_clock, 5)

    conn = db.connect()
    try:
        conn.execute("UPDATE ledger SET detail_json=? WHERE id=3", ('{"code":"tampered"}',))
        conn.commit()
    finally:
        conn.close()

    assert Ledger(db).verify() == 3


def test_deleting_genesis_row_is_reported(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    _append_rows(db, frozen_clock, 5)

    conn = db.connect()
    try:
        conn.execute("DELETE FROM ledger WHERE id=1")
        conn.commit()
    finally:
        conn.close()

    broken_at = Ledger(db).verify()
    assert broken_at == 2
    conn = db.connect()
    try:
        genesis = conn.execute("SELECT MIN(id) AS m FROM ledger").fetchone()["m"]
    finally:
        conn.close()
    assert genesis == 2
