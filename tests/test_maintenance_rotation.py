"""Acceptance tests 8 and 9 (spec 005 5.7): `rotate_ledger` moves every row into an
archive, verified row for row and by its own chain, before any row is deleted;
any failure leaves the source untouched and removes only the archive file this
attempt created.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from petasos.ledger import chain
from petasos.sessions import maintenance
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


def _seed_ledger_rows(db: Database, *, count: int, clock) -> None:
    with db.transaction() as conn:
        for i in range(count):
            chain.append(
                conn,
                now=clock(),
                kind="staged",
                scope="s",
                actor="a",
                verb=None,
                tier=None,
                grant_id=None,
                record_hash=None,
                detail={"i": i, "padding": "x" * 50},
            )


def test_rotation_moves_rows_to_an_archive_and_appends_one_marker_row(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    monkeypatch.setattr(maintenance, "ROTATION_BATCH_ROWS", 7)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)

    report = maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())

    assert report is not None
    assert report.archive == archive_dir / "ledger-20260101T000000Z.sqlite"
    assert report.rows == 25
    assert report.first_id == 1
    assert report.last_id == 25

    archive_conn = sqlite3.connect(report.archive)
    archive_conn.row_factory = sqlite3.Row
    try:
        archive_rows = archive_conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
        assert len(archive_rows) == 25
        assert [row["id"] for row in archive_rows] == list(range(1, 26))
        assert chain.verify(archive_conn) is None
        assert archive_rows[-1]["hash"] == report.last_hash
    finally:
        archive_conn.close()

    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
        assert len(rows) == 1
        marker = rows[0]
        assert marker["id"] == 26
        assert marker["kind"] == "maintenance"
        assert marker["prev_hash"] == chain.GENESIS_HASH
        detail = json.loads(marker["detail_json"])
        assert detail == {
            "code": "rotated",
            "archived_rows": 25,
            "archived_first_id": 1,
            "archived_last_id": 25,
            "archived_last_hash": report.last_hash,
            "archive": report.archive.name,
        }
        assert chain.verify(conn) is None

        chain.append(
            conn,
            now=frozen_clock(),
            kind="staged",
            scope="s",
            actor="a",
            verb=None,
            tier=None,
            grant_id=None,
            record_hash=None,
            detail={},
        )
    finally:
        conn.close()

    conn = db.connect()
    try:
        last_two = conn.execute(
            "SELECT id, prev_hash FROM ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert last_two["id"] == 27
        assert last_two["prev_hash"] == marker["hash"]
    finally:
        conn.close()


def test_rotation_below_threshold_returns_none_and_writes_nothing(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=19, clock=frozen_clock)

    report = maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())
    assert report is None
    assert list(archive_dir.iterdir()) == []


def test_preexisting_archive_file_raises_and_leaves_everything_untouched(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)

    archive_path = archive_dir / "ledger-20260101T000000Z.sqlite"
    archive_path.write_bytes(b"pre-existing content")
    before_mtime = archive_path.stat().st_mtime_ns
    before_bytes = archive_path.read_bytes()

    with pytest.raises(FileExistsError):
        maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())

    assert archive_path.read_bytes() == before_bytes
    assert archive_path.stat().st_mtime_ns == before_mtime

    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
    finally:
        conn.close()
    assert count == 25

    report = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)
    assert report.errors == ("rotate",)


def test_rotation_succeeds_under_a_new_name_after_the_clock_advances(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)
    (archive_dir / "ledger-20260101T000000Z.sqlite").write_bytes(b"collision")

    frozen_clock.advance(1)
    report = maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())
    assert report is not None
    assert report.archive == archive_dir / "ledger-20260101T000001Z.sqlite"


def test_two_concurrent_rotations_only_one_rotates(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)

    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(
                maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    assert sorted(r is None for r in results) == [False, True]

    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM ledger").fetchall()
        assert len(rows) == 1
        assert rows[0]["kind"] == "maintenance"
    finally:
        conn.close()

    archives = list(archive_dir.iterdir())
    assert len(archives) == 1
    archive_conn = sqlite3.connect(archives[0])
    archive_conn.row_factory = sqlite3.Row
    try:
        assert chain.verify(archive_conn) is None
    finally:
        archive_conn.close()


def test_broken_source_chain_refuses_rotation_and_verify_still_names_the_row(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)

    conn = db.connect()
    try:
        conn.execute("UPDATE ledger SET hash='corrupted' WHERE id=10")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError):
        maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())

    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
        assert count == 25
        assert chain.verify(conn) == 10
    finally:
        conn.close()
    assert list(archive_dir.iterdir()) == []


def test_a_row_dropped_from_the_archive_after_copy_is_caught_and_rolled_back(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)

    real_copy_rows = maintenance._copy_rows

    def corrupting_copy_rows(conn, archive_conn, digest):
        result = real_copy_rows(conn, archive_conn, digest)
        archive_conn.execute("DELETE FROM ledger WHERE id = (SELECT MAX(id) FROM ledger)")
        return result

    monkeypatch.setattr(maintenance, "_copy_rows", corrupting_copy_rows)

    with pytest.raises(RuntimeError):
        maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())

    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
        assert count == 25
        assert chain.verify(conn) is None
    finally:
        conn.close()
    assert list(archive_dir.iterdir()) == []


def test_chain_append_raising_leaves_source_intact_and_removes_the_archive(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "LEDGER_ROTATE_ROWS", 20)
    _seed_ledger_rows(db, count=25, clock=frozen_clock)

    def raise_append(*args, **kwargs):
        raise RuntimeError("append boom")

    monkeypatch.setattr(maintenance.chain, "append", raise_append)

    with pytest.raises(RuntimeError):
        maintenance.rotate_ledger(db, archive_dir=archive_dir, now=frozen_clock())

    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
        assert count == 25
        assert chain.verify(conn) is None
    finally:
        conn.close()
    assert list(archive_dir.iterdir()) == []
