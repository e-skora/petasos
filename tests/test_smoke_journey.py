"""Acceptance test 11 (spec 005): the smoke journey against the two explicit
receipt configurations `support_005` builds, so its meaning does not depend on
whether change 004 has merged. Loads `scripts/smoke_journey.py` by path with
`importlib` since it lives outside any package (the same idiom as
`tests/test_release_gate.py`). Nothing here opens the network or sleeps for real.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from support_005 import lifespan_harness, receipt_absent, receipt_provided

from petasos.helpdesk import fake_payments
from petasos.serve import Settings, build_app
from petasos.sessions.store import MintCeilingExceeded, SessionStore
from petasos.storage import Database
from petasos.trust.grants import GrantStore
from petasos.trust.outcomes import make_result

if TYPE_CHECKING:
    from conftest import FrozenClock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_journey.py"
_SPEC = importlib.util.spec_from_file_location("smoke_journey", MODULE_PATH)
smoke_journey = importlib.util.module_from_spec(_SPEC)
sys.modules["smoke_journey"] = smoke_journey
_SPEC.loader.exec_module(smoke_journey)

BASE_URL = "http://testserver"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "p.sqlite"


@pytest.fixture
def db(db_path: Path) -> Database:
    database = Database(db_path)
    database.migrate()
    return database


def _settings(tmp_path: Path, db_path: Path) -> Settings:
    return Settings(db_path=db_path, archive_dir=tmp_path / "archive", host="0.0.0.0", port=8080)


@asynccontextmanager
async def _running_client(app):
    async with (
        lifespan_harness(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        yield client


async def test_receipt_absent_ends_unconfirmed(
    db: Database, db_path: Path, frozen_clock: FrozenClock, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, db_path)
    app = receipt_absent(build_app(settings, clock=frozen_clock))
    async with _running_client(app) as client:
        report = await smoke_journey.run_journey(client, base_url=BASE_URL)
    assert report.outcome == "receipt-unavailable"
    assert report.exit_code == 4


async def test_receipt_provided_confirms_with_no_token_leak(
    db: Database,
    db_path: Path,
    frozen_clock: FrozenClock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    minted_tokens: dict[str, str] = {}
    original_mint = SessionStore.mint

    def spy_mint(self: SessionStore):
        result = original_mint(self)
        minted_tokens.update(result.tokens)
        return result

    monkeypatch.setattr(SessionStore, "mint", spy_mint)
    settings = _settings(tmp_path, db_path)
    app = receipt_provided(build_app(settings, clock=frozen_clock), db)
    async with _running_client(app) as client:
        report = await smoke_journey.run_journey(client, base_url=BASE_URL)

    assert report.outcome == "confirmed"
    assert report.exit_code == 0
    assert report.session_id is not None

    conn = db.connect()
    try:
        grant_tokens = [
            row["token"]
            for row in conn.execute("SELECT token FROM grants WHERE token IS NOT NULL").fetchall()
        ]
    finally:
        conn.close()

    captured = capsys.readouterr()
    assert minted_tokens, "the journey must have minted a session"
    for value in (*minted_tokens.values(), *grant_tokens):
        assert value not in captured.out
        assert value not in captured.err


async def test_refund_effect_writing_nothing_fails_the_receipt_step(
    db: Database,
    db_path: Path,
    frozen_clock: FrozenClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fake_payments, "record_refund", lambda *a, **k: None)
    settings = _settings(tmp_path, db_path)
    app = receipt_provided(build_app(settings, clock=frozen_clock), db)
    async with _running_client(app) as client:
        report = await smoke_journey.run_journey(client, base_url=BASE_URL)
    assert report.exit_code == 1
    assert report.outcome == "failed"


async def test_a_second_refund_row_fails_the_second_receipt_read(
    db: Database,
    db_path: Path,
    frozen_clock: FrozenClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_read_receipt = smoke_journey._read_receipt
    calls = {"n": 0}

    async def spying_read_receipt(client, *, owner_token):
        calls["n"] += 1
        if calls["n"] == 2:
            conn = db.connect()
            try:
                scope = conn.execute("SELECT id FROM sessions LIMIT 1").fetchone()["id"]
                ticket_id = conn.execute(
                    "SELECT id FROM tickets WHERE scope=? AND number=3", (scope,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT INTO helpdesk_fake_refunds "
                    "(scope, ticket_id, amount_minor, currency, grant_key, refunded_at) "
                    "VALUES (?, ?, 4200, 'USD', 'extra-key', ?)",
                    (scope, ticket_id, frozen_clock().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
        return await real_read_receipt(client, owner_token=owner_token)

    monkeypatch.setattr(smoke_journey, "_read_receipt", spying_read_receipt)
    settings = _settings(tmp_path, db_path)
    app = receipt_provided(build_app(settings, clock=frozen_clock), db)
    async with _running_client(app) as client:
        report = await smoke_journey.run_journey(client, base_url=BASE_URL)
    assert report.exit_code == 1
    assert report.outcome == "failed"


async def test_session_capacity_response_ends_the_journey_at_exit_3(
    db: Database,
    db_path: Path,
    frozen_clock: FrozenClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_ceiling(self: SessionStore):
        raise MintCeilingExceeded()

    monkeypatch.setattr(SessionStore, "mint", raise_ceiling)
    settings = _settings(tmp_path, db_path)
    app = receipt_provided(build_app(settings, clock=frozen_clock), db)
    async with _running_client(app) as client:
        report = await smoke_journey.run_journey(client, base_url=BASE_URL)
    assert report.outcome == "capacity"
    assert report.exit_code == 3


async def test_approve_refused_not_an_approver_stops_at_that_step(
    db: Database,
    db_path: Path,
    frozen_clock: FrozenClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GrantStore, "approve", lambda self, **kwargs: make_result("refused/not_an_approver")
    )
    settings = _settings(tmp_path, db_path)
    app = receipt_provided(build_app(settings, clock=frozen_clock), db)
    async with _running_client(app) as client:
        report = await smoke_journey.run_journey(client, base_url=BASE_URL)
    assert report.exit_code == 1
    assert report.outcome == "failed"


def test_script_with_no_argument_exits_2_with_one_line_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = smoke_journey.main([])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err.strip().count("\n") == 0
    assert captured.err.strip() != ""
