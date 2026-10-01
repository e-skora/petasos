"""The ledger view: rows_for, archived_before_id, and /owner/ledger,
/owner/ledger/verify end to end (spec 4.9; acceptance tests 12 and 13)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.ledger.chain import append as ledger_append
from petasos.owner import ledger_view
from petasos.storage import Database


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


@pytest.fixture
def app(db: Database, frozen_clock):
    return create_app(db, clock=frozen_clock)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mint(client: TestClient) -> dict:
    return client.post("/session").json()


def _refund_journey(client: TestClient, minted: dict) -> dict:
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    staged = client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    ).json()
    card = client.get("/owner/approvals", headers=owner_headers).json()["data"][0]
    client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    )
    return {"grant_id": staged["grant_id"], "card": card}


def test_ledger_rows_newest_first_with_no_archive_marker(client) -> None:
    minted = _mint(client)
    _refund_journey(client, minted)
    response = client.get("/owner/ledger", headers=_auth(minted["tokens"]["owner"]))
    payload = response.json()["data"]
    assert payload["archived_before_id"] is None
    ids = [row["id"] for row in payload["rows"]]
    assert ids == sorted(ids, reverse=True)


def test_ledger_rows_have_correct_roles_verb_tier_and_descriptions(client) -> None:
    minted = _mint(client)
    journey = _refund_journey(client, minted)
    rows = client.get("/owner/ledger", headers=_auth(minted["tokens"]["owner"])).json()["data"][
        "rows"
    ]
    by_kind = {row["kind"]: row for row in rows if row["grant_id"] == journey["grant_id"]}
    assert by_kind["staged"]["actor_role"] == "agent"
    assert by_kind["staged"]["verb"] == "ISSUE-REFUND"
    assert by_kind["staged"]["tier"] == "L5"
    assert by_kind["staged"]["description"] == (
        "A refund was requested and is waiting for a person to say yes."
    )
    assert by_kind["approved"]["actor_role"] == "owner"
    assert by_kind["approved"]["description"] == "A person said yes to a refund."
    assert by_kind["executed"]["actor_role"] == "owner"
    assert by_kind["executed"]["description"] == "A refund ran and was recorded."


def test_ledger_rotation_marker_sets_archived_before_id_and_is_excluded(client, db) -> None:
    minted = _mint(client)
    _refund_journey(client, minted)

    with db.transaction() as conn:
        ledger_append(
            conn,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            kind="maintenance",
            scope="global",
            actor="system",
            verb=None,
            tier=None,
            grant_id=None,
            record_hash=None,
            detail={"code": "rotated", "archive": "archive-1"},
        )
        rotation_id = conn.execute(
            "SELECT id FROM ledger WHERE kind='maintenance' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    payload = client.get("/owner/ledger", headers=_auth(minted["tokens"]["owner"])).json()["data"]
    assert payload["archived_before_id"] == rotation_id
    assert all(row["id"] != rotation_id for row in payload["rows"])
    assert all(row["kind"] != "maintenance" for row in payload["rows"])


def test_ledger_pagination_has_no_overlap_and_no_gap(client) -> None:
    minted = _mint(client)
    _refund_journey(client, minted)
    owner_headers = _auth(minted["tokens"]["owner"])
    all_rows = client.get("/owner/ledger?limit=200", headers=owner_headers).json()["data"]["rows"]
    assert len(all_rows) >= 3

    first_page = client.get("/owner/ledger?limit=2", headers=owner_headers).json()["data"]["rows"]
    assert len(first_page) == 2
    last_id = first_page[-1]["id"]
    second_page = client.get(
        f"/owner/ledger?limit=2&before={last_id}", headers=owner_headers
    ).json()["data"]["rows"]
    assert all(row["id"] < last_id for row in second_page)
    combined_ids = [row["id"] for row in first_page] + [row["id"] for row in second_page]
    assert len(combined_ids) == len(set(combined_ids))
    assert combined_ids == [row["id"] for row in all_rows[: len(combined_ids)]]


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=201", "limit=+5", "before=abc", "limit=5&limit=6", "unknown=1"],
)
def test_ledger_bad_query_params_are_400(client, query) -> None:
    minted = _mint(client)
    response = client.get(f"/owner/ledger?{query}", headers=_auth(minted["tokens"]["owner"]))
    assert response.status_code == 400
    assert response.json()["status"] == "refused/invalid_arguments"


def test_ledger_rows_never_leak_seed_text_or_raw_actor_id(client) -> None:
    minted = _mint(client)
    scope = minted["session"]
    _refund_journey(client, minted)
    client.post(
        "/owner/propose",
        headers=_auth(minted["tokens"]["agent"]),
        json={"tool": "add_internal_note", "arguments": {"ticket": 1, "text": "a private note"}},
    )

    payload = client.get("/owner/ledger?limit=200", headers=_auth(minted["tokens"]["owner"])).json()
    blob = json.dumps(payload)

    for forbidden in (
        f"Visitor Three ({scope})",
        f"visitor-three-{scope}@example.invalid",
        "Refund request",
        "a private note",
        f"{scope}-agent",
        f"{scope}-owner",
        f"{scope}-visitor",
    ):
        assert forbidden not in blob


def test_verify_ok_with_no_rotation_marker(client) -> None:
    minted = _mint(client)
    _refund_journey(client, minted)
    response = client.post("/owner/ledger/verify", headers=_auth(minted["tokens"]["owner"]))
    data = response.json()
    assert data["data"]["ok"] is True
    assert data["data"]["archived_before_id"] is None
    assert data["explanation"] == "Every row checks out. The chain is internally consistent."


def test_verify_ok_with_rotation_marker(client, db) -> None:
    minted = _mint(client)
    _refund_journey(client, minted)

    with db.transaction() as conn:
        ledger_append(
            conn,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            kind="maintenance",
            scope="global",
            actor="system",
            verb=None,
            tier=None,
            grant_id=None,
            record_hash=None,
            detail={"code": "rotated", "archive": "archive-1"},
        )

    response = client.post("/owner/ledger/verify", headers=_auth(minted["tokens"]["owner"]))
    data = response.json()
    assert data["data"]["ok"] is True
    assert data["data"]["archived_before_id"] is not None
    assert data["explanation"] == (
        "Every row since the last archive checks out. Older rows were moved to an archive file."
    )


def test_verify_detects_a_tampered_row(client, db) -> None:
    minted = _mint(client)
    _refund_journey(client, minted)

    conn = db.connect()
    try:
        first_row = conn.execute("SELECT id FROM ledger ORDER BY id ASC LIMIT 1").fetchone()
        conn.execute(
            "UPDATE ledger SET detail_json=? WHERE id=?",
            (json.dumps({"code": "tampered"}), first_row["id"]),
        )
    finally:
        conn.close()

    response = client.post("/owner/ledger/verify", headers=_auth(minted["tokens"]["owner"]))
    data = response.json()
    assert data["data"]["ok"] is False
    assert data["data"]["first_bad_row"] == first_row["id"]
    assert str(first_row["id"]) in data["explanation"]
    assert data["explanation"] == (
        f"Row {first_row['id']} does not match what it should. "
        "Something was changed after it was written."
    )


# --- Unit tests for the view module itself --------------------------------------


def test_archived_before_id_picks_the_newest_rotated_row(db) -> None:
    with db.transaction() as conn:
        assert ledger_view.archived_before_id(conn) is None
        ledger_append(
            conn,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            kind="maintenance",
            scope="global",
            actor="system",
            verb=None,
            tier=None,
            grant_id=None,
            record_hash=None,
            detail={"code": "rotated"},
        )
        first_id = conn.execute("SELECT MAX(id) AS m FROM ledger").fetchone()["m"]
        ledger_append(
            conn,
            now=datetime(2026, 1, 2, tzinfo=UTC),
            kind="maintenance",
            scope="global",
            actor="system",
            verb=None,
            tier=None,
            grant_id=None,
            record_hash=None,
            detail={"code": "reset"},
        )
    with db.transaction() as conn:
        assert ledger_view.archived_before_id(conn) == first_id
