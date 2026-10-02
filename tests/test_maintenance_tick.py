"""Acceptance tests 4 to 6 (spec 005 5.5): `run_maintenance`'s three steps, their
failure isolation, and `MaintenanceRunner`'s tick loop and shutdown behavior.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from support_005 import LifespanApp as _LifespanApp
from support_005 import TickGate, lifespan_harness

from petasos.sessions import maintenance
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


def _mint(db: Database, clock) -> str:
    return SessionStore(db, clock=clock).mint().session


def test_run_maintenance_expires_one_of_two_sessions(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    to_expire = _mint(db, frozen_clock)
    frozen_clock.advance(3000)
    live = _mint(db, frozen_clock)
    frozen_clock.advance(601)
    assert to_expire != live

    conn = db.connect()
    try:
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            for table in ("ledger", "canaries", "abuse_counters")
        }
        global_quota_before = [
            dict(row)
            for row in conn.execute("SELECT * FROM quota_events WHERE scope='global'").fetchall()
        ]
    finally:
        conn.close()

    with caplog.at_level(logging.INFO, logger="petasos.sessions.maintenance"):
        report = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)

    assert report.sessions_expired == 1
    assert report.rotation is None
    assert report.archives_pruned == 0
    assert report.errors == ()

    conn = db.connect()
    try:
        remaining = {row["id"] for row in conn.execute("SELECT id FROM sessions").fetchall()}
        after_global_quota = [
            dict(row)
            for row in conn.execute("SELECT * FROM quota_events WHERE scope='global'").fetchall()
        ]
        for table in ("ledger", "canaries", "abuse_counters"):
            after = [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            assert after == before[table]
    finally:
        conn.close()

    assert remaining == {live}
    assert after_global_quota == global_quota_before
    assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1

    second = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)
    assert second.sessions_expired == 0


async def test_maintenance_runner_ticks_once_at_startup_then_once_per_release(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner = _LifespanApp()
    gate = TickGate()
    tick_count = 0
    real_run_maintenance = maintenance.run_maintenance

    def spy(db_arg, *, now, archive_dir, skip_rotation=False):
        nonlocal tick_count
        tick_count += 1
        return real_run_maintenance(
            db_arg, now=now, archive_dir=archive_dir, skip_rotation=skip_rotation
        )

    monkeypatch.setattr(maintenance, "run_maintenance", spy)

    runner = maintenance.MaintenanceRunner(
        inner, db=db, clock=frozen_clock, archive_dir=archive_dir, interval_s=60, sleep=gate
    )

    async with lifespan_harness(runner):
        await gate.wait_until_sleeping()
        assert tick_count == 1
        await gate.release()
        await gate.wait_until_sleeping()
        assert tick_count == 2
        await gate.release()
        await gate.wait_until_sleeping()
        assert tick_count == 3
        await gate.release()
        await gate.wait_until_sleeping()
        assert tick_count == 4

    assert inner.shutdown_seen.is_set()

    http_scope = {"type": "http", "method": "GET", "path": "/healthz", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await runner(http_scope, receive, send)
    assert inner.calls[-1] is http_scope


async def test_maintenance_runner_waits_for_an_in_flight_tick_before_shutdown(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner = _LifespanApp()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_run_maintenance(db_arg, *, now, archive_dir, skip_rotation=False):
        started.set()
        release.wait(5)
        finished.set()
        return maintenance.MaintenanceReport(
            sessions_expired=0, rotation=None, archives_pruned=0, errors=()
        )

    monkeypatch.setattr(maintenance, "run_maintenance", blocking_run_maintenance)

    runner = maintenance.MaintenanceRunner(
        inner,
        db=db,
        clock=frozen_clock,
        archive_dir=archive_dir,
        interval_s=60,
        sleep=asyncio.sleep,
    )

    shutdown_was_early = False

    async def _let_go_shortly() -> None:
        nonlocal shutdown_was_early
        await asyncio.sleep(0.05)
        shutdown_was_early = inner.shutdown_seen.is_set()
        release.set()

    async with lifespan_harness(runner):
        await asyncio.to_thread(started.wait, 5)
        assert not inner.shutdown_seen.is_set()
        let_go_task = asyncio.ensure_future(_let_go_shortly())

    await let_go_task
    assert not shutdown_was_early, "shutdown reached the inner app too early"
    assert finished.is_set()
    assert inner.shutdown_seen.is_set()
