"""Acceptance test 6 (spec 005 5.5): one step's failure never stops the others or
`run_maintenance` itself, and `MaintenanceRunner` backs off rotation for
`ROTATION_BACKOFF_TICKS` ticks after a failed rotation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from support_005 import LifespanApp, TickGate, lifespan_harness

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


def test_expire_failure_is_isolated(
    db: Database,
    archive_dir: Path,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from petasos.sessions.store import SessionStore

    def raise_expire(self, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(SessionStore, "expire", raise_expire)

    with caplog.at_level(logging.WARNING, logger="petasos.sessions.maintenance"):
        report = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)

    assert report.errors == ("expire",)
    assert report.rotation is None
    assert report.archives_pruned == 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "expire" in warnings[0].getMessage()
    for record in warnings:
        assert "boom" not in record.getMessage()


def test_rotate_failure_is_isolated_and_prune_still_runs(
    db: Database,
    archive_dir: Path,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pruned = []
    real_prune = maintenance.prune_archives

    def spy_prune(archive_dir_arg, *, now):
        result = real_prune(archive_dir_arg, now=now)
        pruned.append(result)
        return result

    def raise_rotate(db_arg, *, archive_dir, now):
        raise RuntimeError("rotate boom")

    monkeypatch.setattr(maintenance, "rotate_ledger", raise_rotate)
    monkeypatch.setattr(maintenance, "prune_archives", spy_prune)

    report = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)

    assert report.errors == ("rotate",)
    assert pruned == [0]


def test_prune_failure_is_isolated(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_prune(archive_dir_arg, *, now):
        raise RuntimeError("prune boom")

    monkeypatch.setattr(maintenance, "prune_archives", raise_prune)

    report = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)
    assert report.errors == ("prune",)


def test_all_three_steps_failing_still_returns_a_report_with_no_raise(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from petasos.sessions.store import SessionStore

    monkeypatch.setattr(
        SessionStore, "expire", lambda self, now: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(
        maintenance, "rotate_ledger", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(
        maintenance, "prune_archives", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )

    report = maintenance.run_maintenance(db, now=frozen_clock(), archive_dir=archive_dir)
    assert set(report.errors) == {"expire", "rotate", "prune"}


async def test_runner_backs_off_rotation_after_a_failed_rotation(
    db: Database, archive_dir: Path, frozen_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner = LifespanApp()
    gate = TickGate()
    skip_flags: list[bool] = []
    real_run_maintenance = maintenance.run_maintenance
    should_fail = {"flag": True}

    def spy(db_arg, *, now, archive_dir, skip_rotation=False):
        skip_flags.append(skip_rotation)
        if should_fail["flag"] and not skip_rotation:
            should_fail["flag"] = False
            # A tick that is not skipped but whose rotation fails.
            return maintenance.MaintenanceReport(
                sessions_expired=0, rotation=None, archives_pruned=0, errors=("rotate",)
            )
        return real_run_maintenance(
            db_arg, now=now, archive_dir=archive_dir, skip_rotation=skip_rotation
        )

    monkeypatch.setattr(maintenance, "run_maintenance", spy)
    monkeypatch.setattr(maintenance, "ROTATION_BACKOFF_TICKS", 3)

    runner = maintenance.MaintenanceRunner(
        inner, db=db, clock=frozen_clock, archive_dir=archive_dir, interval_s=60, sleep=gate
    )

    async with lifespan_harness(runner):
        await gate.wait_until_sleeping()  # tick 1: rotate "fails"
        for _ in range(3):
            await gate.release()
            await gate.wait_until_sleeping()  # ticks 2, 3, 4: skipped
        await gate.release()
        await gate.wait_until_sleeping()  # tick 5: not skipped again

    assert skip_flags == [False, True, True, True, False]
