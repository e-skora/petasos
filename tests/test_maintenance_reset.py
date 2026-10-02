"""Acceptance test 7 (spec 005 5.6): `reset_demo` and the `maintenance` command
line's `reset`, `expire`, and `rotate` commands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from petasos.sessions import maintenance
from petasos.sessions.store import SessionStore
from petasos.storage import Database

if TYPE_CHECKING:
    from conftest import FrozenClock

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _mint(db: Database, clock) -> str:
    return SessionStore(db, clock=clock).mint().session


def test_reset_marks_and_expires_three_live_sessions(
    db: Database, frozen_clock: FrozenClock
) -> None:
    for _ in range(3):
        _mint(db, frozen_clock)

    report = maintenance.reset_demo(db, now=frozen_clock())
    assert report.sessions_marked == 3
    assert report.sessions_expired == 3

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT detail_json FROM ledger WHERE kind='maintenance' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        remaining_sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    finally:
        conn.close()
    import json

    detail = json.loads(row["detail_json"])
    assert detail == {"code": "reset", "sessions": 3}
    assert remaining_sessions == 0


def test_reset_with_a_fourth_already_expired_uncleaned_session(
    db: Database, frozen_clock: FrozenClock
) -> None:
    for _ in range(3):
        _mint(db, frozen_clock)
    stale = _mint(db, frozen_clock)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE sessions SET expires_at=? WHERE id=?",
            (frozen_clock().isoformat(), stale),
        )
        conn.commit()
    finally:
        conn.close()

    report = maintenance.reset_demo(db, now=frozen_clock())
    assert report.sessions_marked == 3
    assert report.sessions_expired == 4


def test_reset_step_one_survives_step_two_raising(
    db: Database, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(3):
        _mint(db, frozen_clock)

    conn = db.connect()
    try:
        identity_tokens = [
            (row["id"], "whatever-secret")
            for row in conn.execute("SELECT id FROM identities").fetchall()
        ]
    finally:
        conn.close()
    assert len(identity_tokens) == 9

    def raise_expire(self, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(SessionStore, "expire", raise_expire)

    with pytest.raises(RuntimeError):
        maintenance.reset_demo(db, now=frozen_clock())

    from petasos.mcp.identity import ClientRegistry

    registry = ClientRegistry(db)
    for identity_id, secret in identity_tokens:
        # Step 1 already moved every identity's expires_at to "now", so
        # authentication refuses regardless of the secret (invariant 6: the
        # expiry boundary instant counts as expired).
        assert registry.authenticate(f"{identity_id}.{secret}", now=frozen_clock()) is None

    conn = db.connect()
    try:
        sessions_left = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        identities_left = conn.execute("SELECT COUNT(*) AS n FROM identities").fetchone()["n"]
        for identity_id, _ in identity_tokens:
            row = conn.execute(
                "SELECT expires_at FROM identities WHERE id=?", (identity_id,)
            ).fetchone()
            assert row["expires_at"] == frozen_clock().isoformat()
    finally:
        conn.close()
    assert sessions_left == 3  # step 2 never ran, so the rows are still present
    assert identities_left == 9

    monkeypatch.undo()
    # The next ordinary expiry (not reset) finishes the cleanup step 2 never ran.
    expired_count = SessionStore(db, clock=frozen_clock).expire(frozen_clock())
    assert expired_count == 3
    conn = db.connect()
    try:
        for table in (
            "tickets",
            "notes",
            "helpdesk_fake_mail",
            "helpdesk_fake_refunds",
            "memory_entries",
            "grants",
            "rail_slots",
            "identities",
            "sessions",
        ):
            count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert count == 0, table
    finally:
        conn.close()


def test_reset_cli_subprocess(db: Database, tmp_path: Path) -> None:
    db_path = tmp_path / "p.sqlite"
    database = Database(db_path)
    database.migrate()

    result = subprocess.run(
        [sys.executable, "-m", "petasos.sessions.maintenance", "reset"],
        cwd=REPO_ROOT,
        env={
            "PETASOS_DB": str(db_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "reset: sessions_marked=0 sessions_expired=0"


def test_expire_cli_subprocess(tmp_path: Path) -> None:
    db_path = tmp_path / "p.sqlite"
    Database(db_path).migrate()
    result = subprocess.run(
        [sys.executable, "-m", "petasos.sessions.maintenance", "expire"],
        cwd=REPO_ROOT,
        env={
            "PETASOS_DB": str(db_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "expire: sessions_expired=0"


def test_rotate_cli_subprocess(tmp_path: Path) -> None:
    db_path = tmp_path / "p.sqlite"
    Database(db_path).migrate()
    result = subprocess.run(
        [sys.executable, "-m", "petasos.sessions.maintenance", "rotate"],
        cwd=REPO_ROOT,
        env={
            "PETASOS_DB": str(db_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rotate: archive=none rows=0"


def test_unknown_command_exits_2_with_one_usage_line(tmp_path: Path) -> None:
    db_path = tmp_path / "p.sqlite"
    result = subprocess.run(
        [sys.executable, "-m", "petasos.sessions.maintenance", "bogus"],
        cwd=REPO_ROOT,
        env={
            "PETASOS_DB": str(db_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr.strip().count("\n") == 0


def test_missing_command_exits_2(tmp_path: Path) -> None:
    db_path = tmp_path / "p.sqlite"
    result = subprocess.run(
        [sys.executable, "-m", "petasos.sessions.maintenance"],
        cwd=REPO_ROOT,
        env={
            "PETASOS_DB": str(db_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2


def test_missing_petasos_db_prints_settings_message_and_exits_2() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "petasos.sessions.maintenance", "reset"],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "PETASOS_DB" in result.stderr
