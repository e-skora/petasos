"""Acceptance test 10 (spec 005 5.5): `prune_archives` deletes by age and then by
total budget, oldest first, by the file name's timestamp, never by modification
time; and the 003 spec 3.22 proof that a maintenance tick, a reset, and a rotation
never touch `abuse_counters`, `canaries`, or a `global`-scoped `quota_events` row.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from petasos.sessions import maintenance
from petasos.sessions.quotas import increment_counter
from petasos.sessions.store import SessionStore
from petasos.storage import Database

if TYPE_CHECKING:
    from conftest import FrozenClock


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "p.sqlite")
    database.migrate()
    return database


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    path = tmp_path / "archive"
    path.mkdir()
    return path


def _archive_named_days_old(archive_dir: Path, *, now, days: int, size: int = 10) -> Path:
    ts = now - timedelta(days=days)
    name = f"ledger-{ts:%Y%m%dT%H%M%SZ}.sqlite"
    path = archive_dir / name
    path.write_bytes(b"x" * size)
    return path


def test_prune_deletes_strictly_older_than_keep_days_and_keeps_the_boundary(
    archive_dir: Path, frozen_clock: FrozenClock
) -> None:
    now = frozen_clock()
    old = _archive_named_days_old(archive_dir, now=now, days=91)
    boundary = _archive_named_days_old(archive_dir, now=now, days=90)
    fresh = _archive_named_days_old(archive_dir, now=now, days=89)
    note = archive_dir / "notes.txt"
    note.write_text("hello")
    garbage = archive_dir / "ledger-garbage.sqlite"
    garbage.write_bytes(b"whatever")

    pruned = maintenance.prune_archives(archive_dir, now=now)

    assert pruned == 1
    assert not old.exists()
    assert boundary.exists()
    assert fresh.exists()
    assert note.exists()
    assert garbage.exists()


def test_prune_by_budget_deletes_oldest_first(
    archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = frozen_clock()
    a = _archive_named_days_old(archive_dir, now=now, days=3, size=100)
    b = _archive_named_days_old(archive_dir, now=now, days=2, size=100)
    c = _archive_named_days_old(archive_dir, now=now, days=1, size=100)

    monkeypatch.setattr(maintenance, "ARCHIVE_BUDGET_BYTES", 150)
    pruned = maintenance.prune_archives(archive_dir, now=now)

    assert pruned == 2
    assert not a.exists()
    assert not b.exists()
    assert c.exists()


def test_name_matching_pattern_but_not_a_valid_timestamp_is_skipped(
    archive_dir: Path, frozen_clock: FrozenClock
) -> None:
    bogus = archive_dir / "ledger-20269999T999999Z.sqlite"
    bogus.write_bytes(b"x")

    pruned = maintenance.prune_archives(archive_dir, now=frozen_clock())

    assert pruned == 0
    assert bogus.exists()


def _counts(db: Database) -> dict[str, list[dict]]:
    conn = db.connect()
    try:
        return {
            "abuse_counters": [
                dict(r) for r in conn.execute("SELECT * FROM abuse_counters").fetchall()
            ],
            "canaries": [dict(r) for r in conn.execute("SELECT * FROM canaries").fetchall()],
            "global_quota_events": [
                dict(r)
                for r in conn.execute("SELECT * FROM quota_events WHERE scope='global'").fetchall()
            ],
        }
    finally:
        conn.close()


def test_global_state_survives_a_tick_a_reset_and_a_rotation(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    SessionStore(db, clock=frozen_clock).mint()
    conn = db.connect()
    try:
        increment_counter(conn, "sessions_minted")
        conn.commit()
    finally:
        conn.close()

    before = _counts(db)
    assert before["abuse_counters"]  # the mint above already raised a high-water value

    frozen_clock.advance(25 * 3600)
    maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)
    assert _counts(db) == before

    frozen_clock.advance(8 * 24 * 3600)
    maintenance.reset_demo(db, now=frozen_clock())
    assert _counts(db) == before

    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 0)
    frozen_clock.advance(100 * 24 * 3600)
    maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())
    assert _counts(db) == before
