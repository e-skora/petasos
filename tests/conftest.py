"""Shared fixtures. See ARCHITECTURE.md section 10: inject the clock into anything that
computes expiry, and derive test dates from that clock, rather than writing a frozen-NOW-
relative date constant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


class FrozenClock:
    """A callable clock fixed at construction time, advanced explicitly by tests.

    Call the fixture itself to read the current fixed time: `frozen_clock()`. Call
    `frozen_clock.advance(seconds)` to move it forward, for example to push a grant past its
    TTL. The time is always an aware UTC datetime.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def tmp_sqlite(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite"
