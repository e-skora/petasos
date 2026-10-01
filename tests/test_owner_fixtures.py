"""The fixture contract for change 007 (spec 4.5 test 17): a named-scenario map,
`FIXTURES`, a placeholder-redaction pass over dynamic values, and a checker that
compares a committed fixture against a fresh live response by key set, by JSON
type, and by exact value for `status`, `verb`, `tier`, and `explanation`.

`demo/package.json` does not exist yet (change 007 has not built), so fixtures are
not mandatory: this test produces the redacted live responses as the reference set
under `tmp_path` and passes. The checker's own teeth are proven separately, against
synthetic fixtures, regardless of whether `demo/` exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from petasos.app import create_app
from petasos.ledger.chain import append as ledger_append
from petasos.owner import api as owner_api
from petasos.storage import Database

REPO_ROOT = Path(__file__).resolve().parents[1]

FIXTURES: dict[str, str] = {
    "session.json": "POST /session (mint)",
    "me-visitor.json": "GET /owner/me as visitor",
    "me-owner.json": "GET /owner/me as owner",
    "tickets.json": "GET /owner/tickets as visitor (six items)",
    "ticket-3-empty.json": "GET /owner/tickets/3 (no activity)",
    "ticket-3-active.json": "GET /owner/tickets/3 (one note, one mail row, one refund row)",
    "ticket-2.json": "GET /owner/tickets/2 (the seeded system note)",
    "propose-staged-refund.json": "POST /owner/propose issue_refund as agent -> staged",
    "propose-staged-reply.json": "POST /owner/propose reply_to_customer as agent -> staged",
    "propose-executed-note.json": "POST /owner/propose add_internal_note as agent -> executed",
    "propose-refused-invalid.json": "POST /owner/propose with a bad shape -> refused/invalid_arguments",
    "propose-refused-rail-full.json": "POST /owner/propose past the SEND-EMAIL rail -> refused/rail_full",
    "propose-not-allowed.json": "POST /owner/propose with a read tool name -> refused/not_allowed",
    "approvals-two-cards.json": "GET /owner/approvals: a refund card and a reply card",
    "approvals-deletion.json": "GET /owner/approvals: one DELETE-TICKET card, tier L5",
    "approvals-deleted-ticket.json": "GET /owner/approvals: a refund card for a deleted ticket",
    "approvals-empty.json": "GET /owner/approvals with nothing staged",
    "approvals-forbidden.json": "GET /owner/approvals as agent -> 403",
    "approve-executed.json": "POST /owner/approve -> executed",
    "approve-stale.json": "POST /owner/approve -> refused/stale_resource",
    "approve-self.json": "POST /owner/approve -> refused/self_approval",
    "approve-verb-mismatch.json": "POST /owner/approve with the wrong verb -> refused/verb_mismatch",
    "approve-used.json": "POST /owner/approve a second time -> refused/unknown_or_used_or_expired",
    "abort-aborted.json": "POST /owner/abort -> aborted",
    "abort-all.json": "POST /owner/abort-all -> aborted, data.aborted 2",
    "ledger-refund.json": "GET /owner/ledger: staged, approved, executed, a READ row, a guard row",
    "ledger-archived.json": "GET /owner/ledger with archived_before_id set",
    "verify-ok.json": "POST /owner/ledger/verify -> ok true, archived_before_id null",
    "verify-archived.json": "POST /owner/ledger/verify -> ok true, archived_before_id set",
    "verify-broken.json": "POST /owner/ledger/verify -> ok false, first_bad_row set",
    "guard-409.json": "GET /owner/tickets/7 -> the seam's 409",
    "unauthenticated.json": "GET /owner/me with no token -> 401",
    "quota.json": "GET /owner/approvals past the browser_reads quota -> 429",
    "origin-refused.json": "GET /owner/tickets with a disallowed Origin -> 403",
    "too-large.json": "POST /owner/propose with an oversized body -> 413",
}

_DYNAMIC_KEYS = frozenset(
    {
        "id",
        "grant_id",
        "ts",
        "created_at",
        "sent_at",
        "refunded_at",
        "expires_at",
        "seconds_left",
        "token",
        "session",
        "tokens",
        "first_bad_row",
    }
)


def _redact_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in _DYNAMIC_KEYS and value is not None:
                out[key] = f"<{key}>"
            else:
                out[key] = _redact_keys(value)
        return out
    if isinstance(obj, list):
        return [_redact_keys(v) for v in obj]
    return obj


def _redact_strings(obj: Any, needles: list[tuple[str, str]]) -> Any:
    if isinstance(obj, dict):
        return {k: _redact_strings(v, needles) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_strings(v, needles) for v in obj]
    if isinstance(obj, str):
        out = obj
        for needle, placeholder in needles:
            if needle:
                out = out.replace(needle, placeholder)
        return out
    return obj


def redact(payload: Any, *, needles: list[tuple[str, str]]) -> Any:
    return _redact_strings(_redact_keys(payload), needles)


def _needles_for(session_id: str, tokens: dict[str, str]) -> list[tuple[str, str]]:
    pairs = [(session_id, "<SESSION>")]
    for role, tok in tokens.items():
        pairs.append((tok, f"<TOKEN:{role}>"))
        pairs.append((tok.split(".")[0], f"<ID:{role}>"))
    return pairs


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mint(client: TestClient) -> dict:
    return client.post("/session").json()


def _produce_all(client: TestClient, db: Database) -> dict[str, dict]:
    live: dict[str, dict] = {}

    # --- main session: me, tickets, the happy refund+reply journey -----------
    main = _mint(client)
    main_needles = _needles_for(main["session"], main["tokens"])
    v, a, o = (_auth(main["tokens"][r]) for r in ("visitor", "agent", "owner"))

    live["session.json"] = redact(main, needles=main_needles)
    live["me-visitor.json"] = redact(
        client.get("/owner/me", headers=v).json(), needles=main_needles
    )
    live["me-owner.json"] = redact(client.get("/owner/me", headers=o).json(), needles=main_needles)
    live["tickets.json"] = redact(
        client.get("/owner/tickets", headers=v).json(), needles=main_needles
    )
    live["ticket-3-empty.json"] = redact(
        client.get("/owner/tickets/3", headers=o).json(), needles=main_needles
    )
    live["ticket-2.json"] = redact(
        client.get("/owner/tickets/2", headers=o).json(), needles=main_needles
    )

    refund_staged = client.post(
        "/owner/propose",
        headers=a,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    live["propose-staged-refund.json"] = redact(refund_staged.json(), needles=main_needles)

    reply_staged = client.post(
        "/owner/propose",
        headers=a,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 4, "body": "hi there"}},
    )
    live["propose-staged-reply.json"] = redact(reply_staged.json(), needles=main_needles)

    two_cards = client.get("/owner/approvals", headers=o)
    live["approvals-two-cards.json"] = redact(two_cards.json(), needles=main_needles)

    cards = two_cards.json()["data"]
    refund_card = next(c for c in cards if c["verb"] == "ISSUE-REFUND")
    reply_card = next(c for c in cards if c["verb"] == "SEND-EMAIL")

    approve_executed = client.post(
        "/owner/approve", headers=o, json={"token": refund_card["token"], "verb": "ISSUE-REFUND"}
    )
    live["approve-executed.json"] = redact(approve_executed.json(), needles=main_needles)

    approve_used = client.post(
        "/owner/approve", headers=o, json={"token": refund_card["token"], "verb": "ISSUE-REFUND"}
    )
    live["approve-used.json"] = redact(approve_used.json(), needles=main_needles)

    client.post(
        "/owner/approve", headers=o, json={"token": reply_card["token"], "verb": "SEND-EMAIL"}
    )
    note_executed = client.post(
        "/owner/propose",
        headers=a,
        json={"tool": "add_internal_note", "arguments": {"ticket": 3, "text": "checking"}},
    )
    live["propose-executed-note.json"] = redact(note_executed.json(), needles=main_needles)

    live["ticket-3-active.json"] = redact(
        client.get("/owner/tickets/3", headers=o).json(), needles=main_needles
    )

    guard_409 = client.get("/owner/tickets/7", headers=o)
    live["guard-409.json"] = redact(guard_409.json(), needles=main_needles)

    ledger_resp = client.get("/owner/ledger", headers=o)
    live["ledger-refund.json"] = redact(ledger_resp.json(), needles=main_needles)

    verify_ok = client.post("/owner/ledger/verify", headers=o)
    live["verify-ok.json"] = redact(verify_ok.json(), needles=main_needles)

    # --- abort and abort-all --------------------------------------------------
    abort_session = _mint(client)
    abort_needles = _needles_for(abort_session["session"], abort_session["tokens"])
    ao, aa = _auth(abort_session["tokens"]["owner"]), _auth(abort_session["tokens"]["agent"])
    client.post(
        "/owner/propose",
        headers=aa,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "11.00", "currency": "USD"},
        },
    )
    abort_card = client.get("/owner/approvals", headers=ao).json()["data"][0]
    aborted = client.post("/owner/abort", headers=ao, json={"token": abort_card["token"]})
    live["abort-aborted.json"] = redact(aborted.json(), needles=abort_needles)

    abort_all_session = _mint(client)
    abort_all_needles = _needles_for(abort_all_session["session"], abort_all_session["tokens"])
    alo, ala = (
        _auth(abort_all_session["tokens"]["owner"]),
        _auth(abort_all_session["tokens"]["agent"]),
    )
    client.post(
        "/owner/propose",
        headers=ala,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "11.00", "currency": "USD"},
        },
    )
    client.post(
        "/owner/propose",
        headers=ala,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 4, "body": "hi"}},
    )
    second_card = client.get("/owner/approvals", headers=alo).json()["data"][0]
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE grants SET state='APPROVED', approved_by=?, approved_at=? WHERE id=?",
            (
                f"{abort_all_session['session']}-owner",
                "2026-01-01T00:00:00+00:00",
                second_card["grant_id"],
            ),
        )
    finally:
        conn.close()
    abort_all_resp = client.post("/owner/abort-all", headers=alo)
    live["abort-all.json"] = redact(abort_all_resp.json(), needles=abort_all_needles)

    # --- stale resource --------------------------------------------------------
    stale_session = _mint(client)
    stale_needles = _needles_for(stale_session["session"], stale_session["tokens"])
    so, sa = _auth(stale_session["tokens"]["owner"]), _auth(stale_session["tokens"]["agent"])
    client.post(
        "/owner/propose",
        headers=sa,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    stale_card = client.get("/owner/approvals", headers=so).json()["data"][0]
    client.post(
        "/owner/propose",
        headers=sa,
        json={"tool": "add_internal_note", "arguments": {"ticket": 3, "text": "changed"}},
    )
    stale_result = client.post(
        "/owner/approve", headers=so, json={"token": stale_card["token"], "verb": "ISSUE-REFUND"}
    )
    live["approve-stale.json"] = redact(stale_result.json(), needles=stale_needles)

    # --- self approval -----------------------------------------------------------
    self_session = _mint(client)
    self_needles = _needles_for(self_session["session"], self_session["tokens"])
    self_owner = _auth(self_session["tokens"]["owner"])
    client.post(
        "/owner/propose",
        headers=self_owner,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    self_card = client.get("/owner/approvals", headers=self_owner).json()["data"][0]
    self_result = client.post(
        "/owner/approve",
        headers=self_owner,
        json={"token": self_card["token"], "verb": "ISSUE-REFUND"},
    )
    live["approve-self.json"] = redact(self_result.json(), needles=self_needles)

    # --- verb mismatch -------------------------------------------------------------
    verb_session = _mint(client)
    verb_needles = _needles_for(verb_session["session"], verb_session["tokens"])
    vo, va = _auth(verb_session["tokens"]["owner"]), _auth(verb_session["tokens"]["agent"])
    client.post(
        "/owner/propose",
        headers=va,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    verb_card = client.get("/owner/approvals", headers=vo).json()["data"][0]
    verb_result = client.post(
        "/owner/approve", headers=vo, json={"token": verb_card["token"], "verb": "DELETE-TICKET"}
    )
    live["approve-verb-mismatch.json"] = redact(verb_result.json(), needles=verb_needles)

    # --- deletion cards --------------------------------------------------------------
    del_session = _mint(client)
    del_needles = _needles_for(del_session["session"], del_session["tokens"])
    do, da = _auth(del_session["tokens"]["owner"]), _auth(del_session["tokens"]["agent"])
    client.post(
        "/owner/propose",
        headers=da,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    client.post(
        "/owner/propose", headers=da, json={"tool": "delete_ticket", "arguments": {"ticket": 3}}
    )
    pre_delete = client.get("/owner/approvals", headers=do)
    live["approvals-deletion.json"] = redact(pre_delete.json(), needles=del_needles)
    delete_card = next(c for c in pre_delete.json()["data"] if c["verb"] == "DELETE-TICKET")
    client.post(
        "/owner/approve", headers=do, json={"token": delete_card["token"], "verb": "DELETE-TICKET"}
    )
    post_delete = client.get("/owner/approvals", headers=do)
    live["approvals-deleted-ticket.json"] = redact(post_delete.json(), needles=del_needles)

    # --- rail full ---------------------------------------------------------------------
    rail_session = _mint(client)
    rail_needles = _needles_for(rail_session["session"], rail_session["tokens"])
    ra = _auth(rail_session["tokens"]["agent"])
    for ticket in range(1, 6):
        client.post(
            "/owner/propose",
            headers=ra,
            json={"tool": "reply_to_customer", "arguments": {"ticket": ticket, "body": "hi"}},
        )
    rail_full = client.post(
        "/owner/propose",
        headers=ra,
        json={"tool": "reply_to_customer", "arguments": {"ticket": 6, "body": "hi"}},
    )
    live["propose-refused-rail-full.json"] = redact(rail_full.json(), needles=rail_needles)

    # --- not allowed / invalid / forbidden / empty --------------------------------------
    misc_session = _mint(client)
    misc_needles = _needles_for(misc_session["session"], misc_session["tokens"])
    mo, ma = _auth(misc_session["tokens"]["owner"]), _auth(misc_session["tokens"]["agent"])
    not_allowed = client.post(
        "/owner/propose", headers=ma, json={"tool": "list_tickets", "arguments": {}}
    )
    live["propose-not-allowed.json"] = redact(not_allowed.json(), needles=misc_needles)

    invalid = client.post(
        "/owner/propose",
        headers=ma,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": "3", "amount": "42.00", "currency": "USD"},
        },
    )
    live["propose-refused-invalid.json"] = redact(invalid.json(), needles=misc_needles)

    forbidden = client.get("/owner/approvals", headers=ma)
    live["approvals-forbidden.json"] = redact(forbidden.json(), needles=misc_needles)

    empty = client.get("/owner/approvals", headers=mo)
    live["approvals-empty.json"] = redact(empty.json(), needles=misc_needles)

    # --- archived ledger + broken chain ----------------------------------------------------
    archived_session = _mint(client)
    archived_needles = _needles_for(archived_session["session"], archived_session["tokens"])
    aro, ara = (
        _auth(archived_session["tokens"]["owner"]),
        _auth(archived_session["tokens"]["agent"]),
    )
    client.post(
        "/owner/propose",
        headers=ara,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    arch_card = client.get("/owner/approvals", headers=aro).json()["data"][0]
    client.post(
        "/owner/approve", headers=aro, json={"token": arch_card["token"], "verb": "ISSUE-REFUND"}
    )
    with db.transaction() as conn:
        from datetime import UTC, datetime

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
    live["ledger-archived.json"] = redact(
        client.get("/owner/ledger", headers=aro).json(), needles=archived_needles
    )
    live["verify-archived.json"] = redact(
        client.post("/owner/ledger/verify", headers=aro).json(), needles=archived_needles
    )

    broken_session = _mint(client)
    broken_needles = _needles_for(broken_session["session"], broken_session["tokens"])
    bro, bra = _auth(broken_session["tokens"]["owner"]), _auth(broken_session["tokens"]["agent"])
    client.post(
        "/owner/propose",
        headers=bra,
        json={
            "tool": "issue_refund",
            "arguments": {"ticket": 3, "amount": "42.00", "currency": "USD"},
        },
    )
    conn = db.connect()
    try:
        first_row = conn.execute(
            "SELECT id FROM ledger WHERE scope=? ORDER BY id ASC LIMIT 1",
            (broken_session["session"],),
        ).fetchone()
        conn.execute(
            "UPDATE ledger SET detail_json=? WHERE id=?",
            (json.dumps({"code": "tampered"}), first_row["id"]),
        )
    finally:
        conn.close()
    live["verify-broken.json"] = redact(
        client.post("/owner/ledger/verify", headers=bro).json(), needles=broken_needles
    )

    # --- unauthenticated / origin / too-large / quota ------------------------------------
    live["unauthenticated.json"] = redact(client.get("/owner/me").json(), needles=[])

    origin_session = _mint(client)
    origin_needles = _needles_for(origin_session["session"], origin_session["tokens"])
    origin_refused = client.get(
        "/owner/tickets",
        headers={
            **_auth(origin_session["tokens"]["visitor"]),
            "Origin": "https://evil.example",
        },
    )
    live["origin-refused.json"] = redact(origin_refused.json(), needles=origin_needles)

    too_large_session = _mint(client)
    too_large_needles = _needles_for(too_large_session["session"], too_large_session["tokens"])
    too_large = client.post(
        "/owner/propose",
        headers=_auth(too_large_session["tokens"]["agent"]),
        content=b"x" * (owner_api.MAX_OWNER_BODY_BYTES + 1),
    )
    live["too-large.json"] = redact(too_large.json(), needles=too_large_needles)

    quota_session = _mint(client)
    quota_needles = _needles_for(quota_session["session"], quota_session["tokens"])
    quota_owner = _auth(quota_session["tokens"]["owner"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(owner_api, "BROWSER_READS_PER_HOUR", 1)
        client.get("/owner/approvals", headers=quota_owner)
        quota_refused = client.get("/owner/approvals", headers=quota_owner)
    live["quota.json"] = redact(quota_refused.json(), needles=quota_needles)

    return live


@pytest.fixture
def db(tmp_sqlite: Path) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    return database


@pytest.fixture
def app(db: Database, frozen_clock):
    return create_app(db, clock=frozen_clock)


def test_fixture_contract(app, db, tmp_path: Path) -> None:
    with TestClient(app) as client:
        live = _produce_all(client, db)

    assert set(live) == set(FIXTURES)

    package_marker = REPO_ROOT / "demo" / "package.json"
    fixtures_dir = REPO_ROOT / "demo" / "src" / "fixtures"

    if not package_marker.exists():
        reference_dir = tmp_path / "fixtures-reference"
        reference_dir.mkdir()
        for name, payload in live.items():
            (reference_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True))
        return

    errors = check_fixtures(fixtures_dir, mandatory=True, live=live)
    assert errors == [], "\n".join(errors)


# --- the checker itself, and its own teeth --------------------------------------

_EXACT_MATCH_FIELDS = frozenset({"status", "verb", "tier", "explanation"})


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise TypeError(f"not a JSON value: {value!r}")


def compare(path: str, fixture_value: Any, live_value: Any, errors: list[str]) -> None:
    field = path.rsplit(".", 1)[-1].split("[")[0]
    f_type, l_type = _json_type(fixture_value), _json_type(live_value)
    if f_type != l_type:
        errors.append(f"{path}: type {f_type} in fixture but {l_type} live")
        return
    if isinstance(fixture_value, dict) and isinstance(live_value, dict):
        fkeys, lkeys = set(fixture_value), set(live_value)
        if fkeys != lkeys:
            errors.append(f"{path}: key set {sorted(fkeys)} != {sorted(lkeys)}")
        for key in fkeys & lkeys:
            compare(f"{path}.{key}", fixture_value[key], live_value[key], errors)
    elif isinstance(fixture_value, list) and isinstance(live_value, list):
        if live_value and not fixture_value:
            errors.append(f"{path}: fixture is empty but the live scenario is not")
        if fixture_value and live_value:
            compare(f"{path}[0]", fixture_value[0], live_value[0], errors)
    else:
        if field in _EXACT_MATCH_FIELDS and fixture_value != live_value:
            errors.append(f"{path}: fixture has {fixture_value!r}, live has {live_value!r}")


def check_fixtures(fixtures_dir: Path, *, mandatory: bool, live: dict[str, Any]) -> list[str]:
    if not mandatory:
        return []
    if not fixtures_dir.is_dir():
        return [f"{fixtures_dir} does not exist"]
    existing = {p.name for p in fixtures_dir.glob("*.json")}
    expected = set(FIXTURES)
    errors: list[str] = []
    missing = expected - existing
    extra = existing - expected
    if missing:
        errors.append(f"missing fixtures: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected fixtures: {sorted(extra)}")
    for name in sorted(expected & existing):
        fixture_value = json.loads((fixtures_dir / name).read_text())
        compare(name, fixture_value, live[name], errors)
    return errors


def test_checker_fails_when_fixtures_folder_is_missing(tmp_path: Path) -> None:
    errors = check_fixtures(tmp_path / "does-not-exist", mandatory=True, live={})
    assert errors


def test_checker_passes_when_fixtures_are_not_mandatory(tmp_path: Path) -> None:
    errors = check_fixtures(tmp_path / "does-not-exist", mandatory=False, live={})
    assert errors == []


def test_compare_fails_on_a_renamed_nested_mail_field() -> None:
    live_value = {"to_address": "a@example.invalid", "subject": "s", "sent_at": "t"}
    fixture_value = {"to": "a@example.invalid", "subject": "s", "sent_at": "t"}
    errors: list[str] = []
    compare("mail[0]", fixture_value, live_value, errors)
    assert errors


def test_compare_fails_when_amount_minor_is_a_string() -> None:
    errors: list[str] = []
    compare("record.amount_minor", "4200", 4200, errors)
    assert errors


def test_compare_fails_when_fixture_list_is_empty_but_live_is_not() -> None:
    errors: list[str] = []
    compare("data.refunds", [], [{"amount_minor": 4200}], errors)
    assert errors


def test_compare_fails_when_fixture_customer_is_null_but_live_has_a_name() -> None:
    errors: list[str] = []
    compare("data.customer", None, "Visitor Three", errors)
    assert errors


def test_compare_passes_when_both_are_null_for_a_deleted_ticket() -> None:
    errors: list[str] = []
    compare("data.customer", None, None, errors)
    assert errors == []
