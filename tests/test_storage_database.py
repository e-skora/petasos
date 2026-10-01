"""Database: no file on construction, WAL and busy timeout set, transaction() rolls
back on exception (spec 1.9, acceptance test 38, the storage half)."""

from __future__ import annotations

from pathlib import Path

import pytest

from petasos.storage import Database


def test_construction_creates_no_file(tmp_sqlite: Path) -> None:
    Database(tmp_sqlite)
    assert not tmp_sqlite.exists()


def test_migrate_creates_the_file_and_tables(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    assert tmp_sqlite.exists()

    conn = db.connect()
    try:
        names = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {
        "ledger",
        "grants",
        "rail_slots",
        "memory_entries",
        "canaries",
        "tickets",
        "notes",
        "helpdesk_fake_mail",
        "helpdesk_fake_refunds",
        "sessions",
        "identities",
        "quota_events",
        "abuse_counters",
    } <= names


def test_migrate_is_idempotent(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    db.migrate()


def test_connect_sets_wal_and_busy_timeout(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    conn = db.connect()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_transaction_commits_on_success(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ledger (ts, kind, scope, actor, verb, tier, grant_id, record_hash, "
            "detail_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "2026-01-01T00:00:00+00:00",
                "staged",
                "s",
                "a",
                None,
                None,
                None,
                None,
                "{}",
                "0" * 64,
                "x" * 64,
            ),
        )
    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
    finally:
        conn.close()
    assert count == 1


def test_transaction_rolls_back_on_exception(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()

    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.transaction() as conn:
        conn.execute(
            "INSERT INTO ledger (ts, kind, scope, actor, verb, tier, grant_id, record_hash, "
            "detail_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "2026-01-01T00:00:00+00:00",
                "staged",
                "s",
                "a",
                None,
                None,
                None,
                None,
                "{}",
                "0" * 64,
                "x" * 64,
            ),
        )
        raise Boom("simulated failure")

    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
    finally:
        conn.close()
    assert count == 0
