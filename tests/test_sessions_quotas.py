"""`reserve`: counts and reserves atomically inside the caller's transaction (spec
3.21; acceptance test 23, the store half)."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

from petasos.sessions.quotas import counter_value, increment_counter, reserve
from petasos.storage import Database


def test_reserve_admits_up_to_the_limit_then_refuses(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()

    for _ in range(3):
        with db.transaction() as conn:
            assert reserve(
                conn,
                scope="s1",
                family="calls",
                limit=3,
                window=timedelta(hours=1),
                now=frozen_clock(),
            )

    with db.transaction() as conn:
        assert not reserve(
            conn, scope="s1", family="calls", limit=3, window=timedelta(hours=1), now=frozen_clock()
        )


def test_reserve_is_scoped_independently(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    with db.transaction() as conn:
        for _ in range(3):
            reserve(
                conn,
                scope="s1",
                family="calls",
                limit=3,
                window=timedelta(hours=1),
                now=frozen_clock(),
            )

    with db.transaction() as conn:
        assert reserve(
            conn, scope="s2", family="calls", limit=3, window=timedelta(hours=1), now=frozen_clock()
        )


def test_reserve_admits_again_after_the_window_rolls(tmp_sqlite: Path, frozen_clock) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    with db.transaction() as conn:
        for _ in range(3):
            reserve(
                conn,
                scope="s1",
                family="calls",
                limit=3,
                window=timedelta(hours=1),
                now=frozen_clock(),
            )

    frozen_clock.advance(61 * 60)
    with db.transaction() as conn:
        assert reserve(
            conn, scope="s1", family="calls", limit=3, window=timedelta(hours=1), now=frozen_clock()
        )


def test_two_threads_reserving_the_last_slot_only_one_succeeds(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    now = frozen_clock()
    with db.transaction() as conn:
        reserve(conn, scope="s1", family="calls", limit=2, window=timedelta(hours=1), now=now)

    results: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        with db.transaction() as conn:
            admitted = reserve(
                conn, scope="s1", family="calls", limit=2, window=timedelta(hours=1), now=now
            )
        with lock:
            results.append(admitted)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1


def test_increment_counter_accumulates(tmp_sqlite: Path) -> None:
    db = Database(tmp_sqlite)
    db.migrate()
    with db.transaction() as conn:
        increment_counter(conn, "guard_trips")
        increment_counter(conn, "guard_trips")
        value = counter_value(conn, "guard_trips")
    assert value == 2
