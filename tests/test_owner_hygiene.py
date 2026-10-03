"""Plumbing: routing hygiene, sentence hygiene, import hygiene, and the table set
(spec 4.10, 4.11; acceptance tests 14 and 15)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.mcp.guard_seam import _route_label
from petasos.owner.outcomes import OWNER_SENTENCES
from petasos.storage import Database


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


@pytest.fixture
def app(db: Database, frozen_clock):
    return create_app(db, clock=frozen_clock)


def test_route_label_is_owner_for_any_owner_prefixed_path() -> None:
    assert _route_label("/owner/tickets") == "owner"
    assert _route_label("/owner/tickets/3") == "owner"
    assert _route_label("/owner/anything-unregistered") == "owner"
    assert _route_label("/mcp") == "mcp"


def test_docs_endpoints_are_404(app) -> None:
    with TestClient(app) as client:
        minted = client.post("/session").json()
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = client.get(
                path, headers={"Authorization": f"Bearer {minted['tokens']['owner']}"}
            )
            assert response.status_code == 404


def test_every_owner_sentence_has_no_code_id_or_underscore() -> None:
    for code, sentence in OWNER_SENTENCES.items():
        assert "_" not in sentence, code
        assert code not in sentence, code


def test_too_large_sentence_matches_005_limits_when_present() -> None:
    try:
        import petasos.limits as limits_module
    except ImportError:
        return
    assert limits_module.SENTENCES["refused/too_large"] == OWNER_SENTENCES["refused/too_large"]


def test_importing_owner_has_no_import_time_side_effects(tmp_path: Path) -> None:
    script = (
        "import os\n"
        "before = sorted(os.listdir('.'))\n"
        "import petasos.owner\n"
        "import petasos.owner.api\n"
        "after = sorted(os.listdir('.'))\n"
        "assert before == after, (before, after)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
    assert list(tmp_path.iterdir()) == []


def test_migrate_adds_no_new_table_for_this_change(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.migrate()
    conn = db.connect()
    try:
        names = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    names.discard("sqlite_sequence")
    assert names == {
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
    }
