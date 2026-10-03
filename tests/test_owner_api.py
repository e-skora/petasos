"""Browser endpoints for the demo app (spec 4.1, 4.2, 4.4 to 4.9; acceptance tests
1, 2, 4 to 11, 15, 18, and the owner half of parity test 10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.owner import api as owner_api
from petasos.sessions import quotas
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
    response = client.post("/session")
    assert response.status_code == 201
    return response.json()


def _ledger_count(db: Database, scope: str | None = None) -> int:
    conn = db.connect()
    try:
        if scope is None:
            return conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
        return conn.execute("SELECT COUNT(*) AS n FROM ledger WHERE scope=?", (scope,)).fetchone()[
            "n"
        ]
    finally:
        conn.close()


def _quota_events_count(db: Database, scope: str) -> int:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM quota_events WHERE scope=?", (scope,)
        ).fetchone()["n"]
    finally:
        conn.close()


# --- Test 1: identity, origins, bounds (the identity half) --------------------


def test_owner_me_refuses_bad_or_missing_tokens(client, db, frozen_clock) -> None:
    minted = _mint(client)
    agent_token = minted["tokens"]["agent"]
    other_minted = _mint(client)
    other_agent_id = other_minted["tokens"]["agent"].split(".")[0]

    before = _ledger_count(db)

    bad_headers = [
        {},
        {"Authorization": "Bearer no-dot"},
        {"Authorization": f"Token {agent_token}"},
        {"Authorization": "Bearer  bad.secret"},
        {"Authorization": f"Bearer {agent_token.split('.')[0]}.wrong-secret"},
        {"Authorization": f"Bearer {other_agent_id}.{agent_token.split('.')[1]}"},
    ]
    for headers in bad_headers:
        response = client.get("/owner/me", headers=headers)
        assert response.status_code == 401, headers
        payload = response.json()
        assert payload["status"] == "refused/unauthenticated"
        assert payload["explanation"]

    frozen_clock.advance(61 * 60)
    expired = client.get("/owner/me", headers=_auth(agent_token))
    assert expired.status_code == 401

    after = _ledger_count(db)
    assert before == after


def test_owner_me_returns_role_and_hidden_ticket_number(client, frozen_clock) -> None:
    minted = _mint(client)
    for role in ("visitor", "agent", "owner"):
        response = client.get("/owner/me", headers=_auth(minted["tokens"][role]))
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["role"] == role
        assert data["session"] == minted["session"]
        assert data["expires_at"] == minted["expires_at"]
        assert data["hidden_ticket_number"] == 7
        assert data["seconds_left"] == 3600

    frozen_clock.advance(600)
    response = client.get("/owner/me", headers=_auth(minted["tokens"]["agent"]))
    assert response.json()["data"]["seconds_left"] == 3000


def test_owner_me_never_consumes_a_quota_event(client, db) -> None:
    minted = _mint(client)
    before = _quota_events_count(db, minted["session"])
    for _ in range(5):
        client.get("/owner/me", headers=_auth(minted["tokens"]["agent"]))
    after = _quota_events_count(db, minted["session"])
    assert before == after


# --- Test 2: role gating per endpoint -----------------------------------------

_ENDPOINTS = [
    ("GET", "/owner/me", {"visitor", "agent", "owner"}),
    ("GET", "/owner/tickets", {"visitor", "agent", "owner"}),
    ("GET", "/owner/tickets/3", {"visitor", "agent", "owner"}),
    ("GET", "/owner/approvals", {"owner"}),
    ("GET", "/owner/ledger", {"visitor", "agent", "owner"}),
    ("POST", "/owner/ledger/verify", {"visitor", "agent", "owner"}),
]


@pytest.mark.parametrize("method,path,allowed", _ENDPOINTS)
def test_role_gating_per_endpoint(client, db, method, path, allowed) -> None:
    minted = _mint(client)
    for role in ("visitor", "agent", "owner"):
        before = _ledger_count(db)
        response = client.request(method, path, headers=_auth(minted["tokens"][role]))
        after = _ledger_count(db)
        if role in allowed:
            assert response.status_code != 403, (role, path)
        else:
            assert response.status_code == 403
            assert response.json()["status"] == "refused/not_allowed"
            assert before == after


def test_propose_role_gating(client, db) -> None:
    minted = _mint(client)
    body = {"tool": "add_internal_note", "arguments": {"ticket": 1, "text": "hi"}}
    visitor_resp = client.post(
        "/owner/propose", headers=_auth(minted["tokens"]["visitor"]), json=body
    )
    assert visitor_resp.status_code == 403
    assert visitor_resp.json()["status"] == "refused/not_allowed"

    for role in ("agent", "owner"):
        resp = client.post("/owner/propose", headers=_auth(minted["tokens"][role]), json=body)
        assert resp.status_code != 403


@pytest.mark.parametrize(
    "tool", ["list_tickets", "get_ticket", "approve", "abort", "ping", "recall", "not-a-tool"]
)
def test_propose_refuses_non_proposable_tool_names_for_every_role(client, db, tool) -> None:
    minted = _mint(client)
    for role in ("visitor", "agent", "owner"):
        before = _ledger_count(db)
        response = client.post(
            "/owner/propose",
            headers=_auth(minted["tokens"][role]),
            json={"tool": tool, "arguments": {}},
        )
        after = _ledger_count(db)
        assert response.status_code == 403
        assert response.json()["status"] == "refused/not_allowed"
        assert before == after


@pytest.mark.parametrize("tool", [["a", "list"], 3])
def test_propose_refuses_non_string_tool_names_for_every_role(client, db, tool) -> None:
    minted = _mint(client)
    for role in ("agent", "owner"):
        before = _ledger_count(db)
        response = client.post(
            "/owner/propose",
            headers=_auth(minted["tokens"][role]),
            json={"tool": tool, "arguments": {}},
        )
        after = _ledger_count(db)
        assert response.status_code == 400
        assert response.json()["status"] == "refused/invalid_arguments"
        assert before == after

    visitor_resp = client.post(
        "/owner/propose",
        headers=_auth(minted["tokens"]["visitor"]),
        json={"tool": tool, "arguments": {}},
    )
    assert visitor_resp.status_code == 403


def test_disallowed_role_approve_leaves_grant_awaiting(client, db) -> None:
    minted = _mint(client)
    staged = client.post(
        "/owner/propose",
        headers=_auth(minted["tokens"]["agent"]),
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    ).json()
    assert staged["status"] == "staged"
    card = client.get("/owner/approvals", headers=_auth(minted["tokens"]["owner"])).json()["data"][
        0
    ]

    for role in ("visitor", "agent"):
        resp = client.post(
            "/owner/approve",
            headers=_auth(minted["tokens"][role]),
            json={"token": card["token"], "verb": "ISSUE-REFUND"},
        )
        assert resp.status_code == 403

    conn = db.connect()
    try:
        row = conn.execute("SELECT state FROM grants WHERE id=?", (card["grant_id"],)).fetchone()
    finally:
        conn.close()
    assert row["state"] == "AWAITING"


# --- Test 4: order of checks, request bounds, and quotas ----------------------


def _padded_invalid_json(total_len: int) -> bytes:
    body = {"tool": "add_internal_note", "arguments": {"ticket": 1, "text": "hi"}}
    base = json.dumps(body).encode()
    assert len(base) < total_len
    return base + b"x" * (total_len - len(base))


def _valid_json_of_length(total_len: int) -> bytes:
    args = {"ticket": 1, "text": "x"}
    body = {"tool": "add_internal_note", "arguments": args}
    current = len(json.dumps(body))
    assert current <= total_len
    args["text"] = "x" * (1 + (total_len - current))
    body = {"tool": "add_internal_note", "arguments": args}
    out = json.dumps(body).encode()
    assert len(out) == total_len
    return out


def test_role_checked_before_body_size_for_propose(client) -> None:
    minted = _mint(client)
    huge = b"x" * 20_000
    response = client.post(
        "/owner/propose",
        headers={**_auth(minted["tokens"]["visitor"]), "Content-Type": "application/json"},
        content=huge,
    )
    assert response.status_code == 403


def test_body_bound_at_the_limit_and_one_byte_over(client, db) -> None:
    minted = _mint(client)
    headers = _auth(minted["tokens"]["agent"])
    max_bytes = owner_api.MAX_OWNER_BODY_BYTES

    before = _ledger_count(db)
    at_limit = client.post(
        "/owner/propose", headers=headers, content=_padded_invalid_json(max_bytes)
    )
    assert at_limit.status_code == 400
    assert at_limit.json()["status"] == "refused/invalid_arguments"
    assert _ledger_count(db) == before

    over_limit = client.post(
        "/owner/propose", headers=headers, content=_padded_invalid_json(max_bytes + 1)
    )
    assert over_limit.status_code == 413
    assert over_limit.json()["status"] == "refused/too_large"


def test_chunked_body_bound(client) -> None:
    minted = _mint(client)
    headers = _auth(minted["tokens"]["agent"])
    max_bytes = owner_api.MAX_OWNER_BODY_BYTES

    def big_gen():
        yield b"x" * (max_bytes + 1)

    too_big = client.post("/owner/propose", headers=headers, content=big_gen())
    assert too_big.status_code == 413
    assert too_big.json()["status"] == "refused/too_large"

    def small_gen():
        body = _valid_json_of_length(100)
        yield body[:50]
        yield body[50:]

    ok = client.post("/owner/propose", headers=headers, content=small_gen())
    assert ok.status_code == 200
    assert ok.json()["status"] == "executed"


def test_unparseable_content_length_still_bounds_body(client) -> None:
    minted = _mint(client)
    headers = {**_auth(minted["tokens"]["agent"]), "content-length": "not-a-number"}
    max_bytes = owner_api.MAX_OWNER_BODY_BYTES

    too_big = client.post("/owner/propose", headers=headers, content=b"x" * (max_bytes + 1))
    assert too_big.status_code == 413
    assert too_big.json()["status"] == "refused/too_large"

    ok = client.post("/owner/propose", headers=headers, content=_valid_json_of_length(100))
    assert ok.status_code == 200
    assert ok.json()["status"] == "executed"


@pytest.mark.parametrize(
    "raw",
    [
        b"[" * 16384,
        b'["a", "b"]',
        b'{"tool": "add_internal_note", "arguments": {}, "extra": 1}',
    ],
)
def test_malformed_propose_bodies_are_invalid_arguments(client, db, raw) -> None:
    minted = _mint(client)
    before = _ledger_count(db)
    response = client.post("/owner/propose", headers=_auth(minted["tokens"]["agent"]), content=raw)
    after = _ledger_count(db)
    assert response.status_code == 400
    assert response.json()["status"] == "refused/invalid_arguments"
    assert before == after


def test_browser_reads_quota_refuses_the_fourth_in_an_hour(client, monkeypatch) -> None:
    monkeypatch.setattr(owner_api, "BROWSER_READS_PER_HOUR", 3)
    minted = _mint(client)
    headers = _auth(minted["tokens"]["owner"])
    for _ in range(3):
        resp = client.get("/owner/approvals", headers=headers)
        assert resp.status_code == 200
    fourth = client.get("/owner/approvals", headers=headers)
    assert fourth.status_code == 429
    assert fourth.json()["status"] == "refused/quota"


def test_calls_quota_shared_across_mcp_and_owner_doors(client, db, monkeypatch) -> None:
    monkeypatch.setattr(quotas, "CALLS_PER_HOUR", 3)
    minted = _mint(client)
    headers = _auth(minted["tokens"]["agent"])

    conn = db.connect()
    try:
        before_calls = quotas.counter_value(conn, "tool_calls")
    finally:
        conn.close()

    first = client.get("/owner/tickets", headers=headers)
    second = client.get("/owner/tickets/3", headers=headers)
    third = client.post(
        "/owner/propose",
        headers=headers,
        json={"tool": "add_internal_note", "arguments": {"ticket": 1, "text": "hi"}},
    )
    fourth = client.get("/owner/tickets", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert fourth.status_code == 429
    assert fourth.json()["status"] == "refused/quota"

    conn = db.connect()
    try:
        after_calls = quotas.counter_value(conn, "tool_calls")
        read_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM ledger WHERE scope=? AND verb='READ'", (minted["session"],)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert after_calls - before_calls == 3
    assert read_rows == 2


def test_verify_quota_refuses_the_second_call(client, monkeypatch) -> None:
    monkeypatch.setattr(owner_api, "VERIFY_PER_HOUR", 1)
    minted = _mint(client)
    headers = _auth(minted["tokens"]["owner"])
    first = client.post("/owner/ledger/verify", headers=headers)
    assert first.status_code == 200
    second = client.post("/owner/ledger/verify", headers=headers)
    assert second.status_code == 429
    assert second.json()["status"] == "refused/quota"


# --- Test 5: tickets and ticket detail -----------------------------------------


def test_tickets_list_hides_the_canary_and_writes_one_read_row(client, db) -> None:
    minted = _mint(client)
    for role in ("visitor", "agent", "owner"):
        response = client.get("/owner/tickets", headers=_auth(minted["tokens"][role]))
        assert response.status_code == 200
        numbers = {t["number"] for t in response.json()["data"]}
        assert numbers == {1, 2, 3, 4, 5, 6}

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT detail_json FROM ledger WHERE scope=? AND kind='executed' AND verb='READ'",
            (minted["session"],),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    for row in rows:
        assert "ticket" not in json.loads(row["detail_json"])


def test_ticket_detail_activity_lifecycle(client) -> None:
    minted = _mint(client)
    owner_headers = _auth(minted["tokens"]["owner"])
    agent_headers = _auth(minted["tokens"]["agent"])

    empty = client.get("/owner/tickets/3", headers=owner_headers).json()["data"]
    assert empty["notes"] == []
    assert empty["mail"] == []
    assert empty["refunds"] == []
    assert "notes" not in empty["ticket"]
    assert "mail" not in empty["ticket"]
    assert "refunds" not in empty["ticket"]

    staged = client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    ).json()
    assert staged["status"] == "staged"
    card = client.get("/owner/approvals", headers=owner_headers).json()["data"][0]
    approved = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    ).json()
    assert approved["status"] == "executed"

    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "add_internal_note", "arguments": {"ticket": 3, "text": "checking"}},
    )

    active = client.get("/owner/tickets/3", headers=owner_headers).json()["data"]
    assert len(active["notes"]) == 1
    assert active["notes"][0]["author_role"] == "agent"
    assert len(active["refunds"]) == 1
    assert active["refunds"][0]["amount_minor"] == 4200
    assert active["mail"] == []


def test_ticket_two_seeded_note_shows_as_system(client) -> None:
    minted = _mint(client)
    data = client.get("/owner/tickets/2", headers=_auth(minted["tokens"]["owner"])).json()["data"]
    assert len(data["notes"]) == 1
    assert data["notes"][0]["author_role"] == "system"


@pytest.mark.parametrize("number", ["0", "abc", "+3", "1_0", "1234567890"])
def test_ticket_detail_bad_number_formats_are_404_with_no_ledger_row(client, db, number) -> None:
    minted = _mint(client)
    before = _ledger_count(db)
    response = client.get(f"/owner/tickets/{number}", headers=_auth(minted["tokens"]["agent"]))
    assert response.status_code == 404
    assert _ledger_count(db) == before


def test_ticket_detail_nonexistent_is_200_refused_with_one_ledger_row(client, db) -> None:
    minted = _mint(client)
    before = _ledger_count(db)
    response = client.get("/owner/tickets/99", headers=_auth(minted["tokens"]["agent"]))
    assert response.status_code == 200
    assert response.json()["status"] == "refused/invalid_arguments"
    assert _ledger_count(db) == before + 1


# --- Test 6: propose, cards, approve -------------------------------------------


def test_propose_refund_stages_with_no_token_and_blocks_self_approval(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    body = {
        "tool": "issue_refund",
        "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
    }

    staged = client.post("/owner/propose", headers=agent_headers, json=body)
    payload = staged.json()
    assert payload["status"] == "staged"
    assert payload["tier"] == "L5"
    assert payload["verb"] == "ISSUE-REFUND"
    assert "token" not in json.dumps(payload)

    owner_staged = client.post("/owner/propose", headers=owner_headers, json=body)
    assert owner_staged.json()["status"] == "staged"
    owner_grant_id = owner_staged.json()["grant_id"]

    cards = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    owner_card_entry = next(c for c in cards if c["grant_id"] == owner_grant_id)
    self_approve = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": owner_card_entry["token"], "verb": "ISSUE-REFUND"},
    )
    assert self_approve.json()["status"] == "refused/self_approval"


def test_propose_note_executes_at_once(client) -> None:
    minted = _mint(client)
    response = client.post(
        "/owner/propose",
        headers=_auth(minted["tokens"]["agent"]),
        json={"tool": "add_internal_note", "arguments": {"ticket": 1, "text": "called back"}},
    )
    assert response.json()["status"] == "executed"


@pytest.mark.parametrize(
    "arguments",
    [
        {"ticket": "3", "amount": "42.00", "currency": "USD"},
        {"ticket": 3, "amount": "42.00", "currency": "USD", "scope": "x"},
        {"ticket": 3, "amount": "42.00"},
        {"ticket": 3, "amount": 42, "currency": "USD"},
    ],
)
def test_propose_shape_refusals_write_no_ledger_row(client, db, arguments) -> None:
    minted = _mint(client)
    before = _ledger_count(db)
    response = client.post(
        "/owner/propose",
        headers=_auth(minted["tokens"]["agent"]),
        json={"tool": "issue_refund", "arguments": arguments},
    )
    assert response.status_code == 400
    assert response.json()["status"] == "refused/invalid_arguments"
    assert _ledger_count(db) == before


@pytest.mark.parametrize(
    "arguments",
    [
        {"ticket": 0, "amount": "42.00", "currency": "USD"},
        {"ticket": 3, "amount": "42.0", "currency": "USD"},
        {"ticket": 3, "amount": "4200.00", "currency": "USD"},
    ],
)
def test_propose_value_refusals_write_one_ledger_row(client, db, arguments) -> None:
    minted = _mint(client)
    before = _ledger_count(db)
    response = client.post(
        "/owner/propose",
        headers=_auth(minted["tokens"]["agent"]),
        json={"tool": "issue_refund", "arguments": arguments},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "refused/invalid_arguments"
    assert _ledger_count(db) == before + 1


# --- Test 7: cards --------------------------------------------------------------


def test_refund_card_shape_and_summary(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    ticket3 = client.get("/owner/tickets/3", headers=owner_headers).json()["data"]["ticket"]

    cards = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    assert len(cards) == 1
    card = cards[0]
    assert card["customer"] == ticket3["customer"]
    assert card["subject"] == ticket3["subject"]
    assert card["record"]["amount_minor"] == 4200
    assert card["summary"] == f"Refund $42.00 USD to {ticket3['customer']} on ticket 3 (version 1)."
    assert "outbound_subject" not in card
    assert card["destination"] == card["record"]["destination"]


def test_reply_card_summary_and_outbound_subject(client, db) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 3, "body": "hello there"}},
    )
    cards = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    reply_card = next(c for c in cards if c["verb"] == "SEND-EMAIL")
    assert reply_card["record"]["arguments"]["body"] == "hello there"
    assert reply_card["summary"].startswith("Send an email to ")

    approved = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": reply_card["token"], "verb": "SEND-EMAIL"},
    )
    assert approved.json()["status"] == "executed"

    conn = db.connect()
    try:
        mail_row = conn.execute(
            "SELECT subject FROM helpdesk_fake_mail WHERE scope=?", (minted["session"],)
        ).fetchone()
    finally:
        conn.close()
    assert reply_card["outbound_subject"] == mail_row["subject"]


def test_approvals_cards_newest_first_with_two_cards(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 4, "body": "hi"}},
    )
    cards = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    assert len(cards) == 2
    assert cards[0]["grant_id"] > cards[1]["grant_id"]


def test_deletion_card_null_customer_and_subject_for_deleted_ticket(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "delete_ticket", "arguments": {"ticket": 3}},
    )
    cards = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    delete_card = next(c for c in cards if c["verb"] == "DELETE-TICKET")
    approve_delete = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": delete_card["token"], "verb": "DELETE-TICKET"},
    )
    assert approve_delete.json()["status"] == "executed"

    cards_after = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    refund_card = next(c for c in cards_after if c["verb"] == "ISSUE-REFUND")
    assert refund_card["customer"] is None
    assert refund_card["subject"] is None
    assert refund_card["summary"] == "Refund $42.00 USD to the customer on ticket 3 (version 1)."


# --- Test 8: approve, abort, abort-all ------------------------------------------


def test_approve_reuse_and_verb_mismatch(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])

    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    card = client.get("/owner/approvals", headers=owner_headers).json()["data"][0]

    executed = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    )
    assert executed.json()["status"] == "executed"

    reused = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    )
    assert reused.json()["status"] == "refused/unknown_or_used_or_expired"

    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 4, "body": "hi"}},
    )
    reply_card = next(
        c
        for c in client.get("/owner/approvals", headers=owner_headers).json()["data"]
        if c["verb"] == "SEND-EMAIL"
    )
    wrong_verb = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": reply_card["token"], "verb": "DELETE-TICKET"},
    )
    assert wrong_verb.json()["status"] == "refused/verb_mismatch"
    burned = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": reply_card["token"], "verb": "SEND-EMAIL"},
    )
    assert burned.json()["status"] == "refused/unknown_or_used_or_expired"


def test_approve_stale_resource_after_note(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    card = client.get("/owner/approvals", headers=owner_headers).json()["data"][0]
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "add_internal_note", "arguments": {"ticket": 3, "text": "checking"}},
    )
    stale = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    )
    assert stale.json()["status"] == "refused/stale_resource"


def test_abort_releases_the_slot(client) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    card = client.get("/owner/approvals", headers=owner_headers).json()["data"][0]
    aborted = client.post("/owner/abort", headers=owner_headers, json={"token": card["token"]})
    assert aborted.json()["status"] == "aborted"
    assert client.get("/owner/approvals", headers=owner_headers).json()["data"] == []


def test_abort_all_counts_both_awaiting_and_approved(client, db) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    client.post(
        "/owner/propose",
        headers=agent_headers,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 4, "body": "hi"}},
    )
    cards = client.get("/owner/approvals", headers=owner_headers).json()["data"]
    second_grant_id = cards[0]["grant_id"]
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE grants SET state='APPROVED', approved_by=?, approved_at=? WHERE id=?",
            (f"{minted['session']}-owner", "2026-01-01T00:00:00+00:00", second_grant_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = client.post("/owner/abort-all", headers=owner_headers)
    payload = result.json()
    assert payload["status"] == "aborted"
    assert payload["data"]["aborted"] == 2
    assert client.get("/owner/approvals", headers=owner_headers).json()["data"] == []

    empty = client.post("/owner/abort-all", headers=owner_headers)
    assert empty.json()["data"]["aborted"] == 0


# --- Test 9: cross-session isolation --------------------------------------------


def test_cross_session_isolation(client) -> None:
    a = _mint(client)
    b = _mint(client)

    client.post(
        "/owner/propose",
        headers=_auth(a["tokens"]["agent"]),
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "10.00", "currency": "USD"},
        },
    )
    card = client.get("/owner/approvals", headers=_auth(a["tokens"]["owner"])).json()["data"][0]

    b_cards = client.get("/owner/approvals", headers=_auth(b["tokens"]["owner"])).json()["data"]
    assert b_cards == []

    approve_from_b = client.post(
        "/owner/approve",
        headers=_auth(b["tokens"]["owner"]),
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    )
    assert approve_from_b.json()["status"] == "refused/unknown_or_used_or_expired"
    abort_from_b = client.post(
        "/owner/abort", headers=_auth(b["tokens"]["owner"]), json={"token": card["token"]}
    )
    assert abort_from_b.json()["status"] == "refused/unknown_or_used_or_expired"
    still_pending = client.get("/owner/approvals", headers=_auth(a["tokens"]["owner"])).json()[
        "data"
    ]
    assert len(still_pending) == 1

    ticket_a = client.get("/owner/tickets/3", headers=_auth(a["tokens"]["owner"])).json()["data"][
        "ticket"
    ]
    ticket_b = client.get("/owner/tickets/3", headers=_auth(b["tokens"]["owner"])).json()["data"][
        "ticket"
    ]
    assert ticket_a["customer"] != ticket_b["customer"]

    ledger_b = client.get("/owner/ledger", headers=_auth(b["tokens"]["owner"])).json()["data"][
        "rows"
    ]
    assert all(row["id"] for row in ledger_b)


# --- Test 10: parity between the MCP card and the owner card -------------------


async def test_parity_between_mcp_and_owner_cards_and_approve(app, db) -> None:
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with app.router.lifespan_context(app):
        http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        )
        minted = (await http_client.post("/session")).json()

        http_client.headers["Authorization"] = f"Bearer {minted['tokens']['agent']}"
        async with (
            streamable_http_client("http://testserver/mcp", http_client=http_client) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            await session.call_tool(
                "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}
            )

        http_client.headers["Authorization"] = f"Bearer {minted['tokens']['owner']}"
        async with (
            streamable_http_client("http://testserver/mcp", http_client=http_client) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            mcp_result = await session.call_tool("list_pending_approvals", {})
            mcp_payload = json.loads(mcp_result.content[0].text)
            mcp_card = mcp_payload["data"][0]

        owner_response = await http_client.get("/owner/approvals")
        owner_cards = owner_response.json()["data"]
    owner_card_entry = owner_cards[0]

    for key in ("grant_id", "token", "verb", "tier", "record", "expires_at"):
        assert mcp_card[key] == owner_card_entry[key]
    assert set(owner_card_entry) - {
        "grant_id",
        "token",
        "verb",
        "tier",
        "record",
        "expires_at",
    } == {
        "customer",
        "subject",
        "destination",
        "summary",
    }


# --- Test 11: guard seam on owner routes ----------------------------------------


def test_canary_ticket_trips_the_guard_for_every_owner_identity(client, db) -> None:
    minted = _mint(client)
    for role in ("visitor", "agent", "owner"):
        conn = db.connect()
        try:
            before_trips = quotas.counter_value(conn, "guard_trips")
        finally:
            conn.close()

        response = client.get("/owner/tickets/7", headers=_auth(minted["tokens"][role]))
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["data"]["status"] == "refused/guard_tripped"
        assert "cn-" not in response.text

        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT detail_json, actor, scope FROM ledger WHERE scope=? AND kind='refused' "
                "ORDER BY id DESC LIMIT 1",
                (minted["session"],),
            ).fetchone()
            after_trips = quotas.counter_value(conn, "guard_trips")
        finally:
            conn.close()
        detail = json.loads(row["detail_json"])
        assert detail["code"] == "guard/canary"
        assert detail["route"] == "owner"
        assert row["actor"] == f"{minted['session']}-{role}"
        assert after_trips == before_trips + 1

    no_token = client.get("/owner/tickets/7")
    assert no_token.status_code == 401


# --- Test 18: reader test -------------------------------------------------------


def test_reader_journey(client, db) -> None:
    minted = _mint(client)
    agent_headers = _auth(minted["tokens"]["agent"])
    owner_headers = _auth(minted["tokens"]["owner"])
    visitor_headers = _auth(minted["tokens"]["visitor"])

    me = client.get("/owner/me", headers=visitor_headers)
    assert me.json()["data"]["role"] == "visitor"

    tickets = client.get("/owner/tickets", headers=visitor_headers)
    assert len(tickets.json()["data"]) == 6

    staged = client.post(
        "/owner/propose",
        headers=agent_headers,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    staged_payload = staged.json()
    assert staged_payload["status"] == "staged"
    assert "token" not in json.dumps(staged_payload)

    approvals = client.get("/owner/approvals", headers=owner_headers)
    cards = approvals.json()["data"]
    assert len(cards) == 1
    card = cards[0]
    assert card["summary"]

    approved = client.post(
        "/owner/approve",
        headers=owner_headers,
        json={"token": card["token"], "verb": "ISSUE-REFUND"},
    )
    assert approved.json()["status"] == "executed"

    detail = client.get("/owner/tickets/3", headers=owner_headers)
    assert len(detail.json()["data"]["refunds"]) == 1

    ledger = client.get("/owner/ledger", headers=owner_headers)
    ledger_payload = ledger.json()
    rows = ledger_payload["data"]["rows"]
    grant_rows = [r for r in rows if r["grant_id"] == card["grant_id"]]
    kinds = {r["kind"] for r in grant_rows}
    assert kinds == {"staged", "approved", "executed"}
    assert ledger_payload["data"]["archived_before_id"] is None

    verify = client.post("/owner/ledger/verify", headers=owner_headers)
    assert verify.json()["data"]["ok"] is True

    full_text = json.dumps(
        [
            me.json(),
            tickets.json(),
            staged_payload,
            approvals.json(),
            approved.json(),
            detail.json(),
        ]
    )
    assert "grant_id" in full_text
