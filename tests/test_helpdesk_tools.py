"""The help-desk `ToolDefinition`s tested directly against a `Gate`, before any HTTP
exists (spec 3.5a, 3.8, 3.14 to 3.18; acceptance tests 9 (tool half), 11, 12
(capture half), 13 (registry half), 25 (tier and verb half))."""

from __future__ import annotations

from pathlib import Path

import pytest

from petasos.helpdesk.data import seed_session
from petasos.helpdesk.fake_payments import has_refund
from petasos.helpdesk.tools import ReadCapture, build_registry
from petasos.storage import Database
from petasos.trust import Gate, Tier

SCOPE = "s1"
OWNER = f"{SCOPE}-owner"
AGENT = f"{SCOPE}-agent"


@pytest.fixture
def db(tmp_sqlite: Path, frozen_clock) -> Database:
    database = Database(tmp_sqlite)
    database.migrate()
    with database.transaction() as conn:
        seed_session(conn, database, SCOPE, frozen_clock)
    return database


def _gate(db: Database, clock) -> tuple[Gate, ReadCapture]:
    capture = ReadCapture()
    registry = build_registry(db, scope=SCOPE, clock=clock, capture=capture)
    return Gate(db, registry, approvers=frozenset({OWNER}), scope=SCOPE, clock=clock), capture


def test_list_tickets_never_includes_the_hidden_ticket_or_bodies(db, frozen_clock) -> None:
    gate, capture = _gate(db, frozen_clock)
    result = gate.propose("list_tickets", {}, proposer=AGENT)
    assert result.code == "executed"
    assert result.tier == Tier.L1
    numbers = [row["number"] for row in capture.data]
    assert numbers == [1, 2, 3, 4, 5, 6]
    assert all("body" not in row for row in capture.data)


def test_get_ticket_returns_the_body_including_the_hidden_ticket(db, frozen_clock) -> None:
    gate, capture = _gate(db, frozen_clock)
    result = gate.propose("get_ticket", {"ticket": 3}, proposer=AGENT)
    assert result.code == "executed"
    assert capture.data["body"]

    gate2, capture2 = _gate(db, frozen_clock)
    hidden = gate2.propose("get_ticket", {"ticket": 7}, proposer=AGENT)
    assert hidden.code == "executed"
    assert "cn-" in capture2.data["body"]


def test_add_internal_note_executes_directly_and_bumps_version(db, frozen_clock) -> None:
    gate, before_capture = _gate(db, frozen_clock)
    gate.propose("get_ticket", {"ticket": 1}, proposer=AGENT)
    before_version = before_capture.data["version"]

    result = gate.propose("add_internal_note", {"ticket": 1, "text": "called back"}, proposer=AGENT)
    assert result.code == "executed"
    assert result.tier == Tier.L2

    gate2, after_capture = _gate(db, frozen_clock)
    gate2.propose("get_ticket", {"ticket": 1}, proposer=AGENT)
    assert after_capture.data["version"] == before_version + 1


def test_reply_and_refund_and_delete_stage_at_expected_tiers(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)

    reply = gate.propose("reply_to_customer", {"ticket": 1, "body": "hello"}, proposer=AGENT)
    assert reply.code == "staged"
    assert reply.tier == Tier.L4
    assert reply.verb == "SEND-EMAIL"

    refund = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}, proposer=AGENT
    )
    assert refund.code == "staged"
    assert refund.tier == Tier.L5
    assert refund.verb == "ISSUE-REFUND"

    delete = gate.propose("delete_ticket", {"ticket": 4}, proposer=AGENT)
    assert delete.code == "staged"
    assert delete.tier == Tier.L5
    assert delete.verb == "DELETE-TICKET"


def test_refund_approval_executes_and_records_one_row(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    staged = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "42.00", "currency": "USD"}, proposer=AGENT
    )
    token = gate.grants.list_pending(viewer=OWNER)[0].token
    approved = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver=OWNER)
    assert approved.code == "approved"
    executed = gate.executor.run(staged.grant_id)
    assert executed.code == "executed"

    conn = db.connect()
    try:
        assert has_refund(conn, scope=SCOPE, ticket_id=3)
        rows = conn.execute("SELECT * FROM helpdesk_fake_refunds").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["amount_minor"] == 4200


def test_over_refund_is_refused(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    result = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "99.00", "currency": "USD"}, proposer=AGENT
    )
    assert result.code == "refused/invalid_arguments"


def test_refund_on_a_ticket_with_no_amount_is_refused(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    result = gate.propose(
        "issue_refund", {"ticket": 1, "amount": "1.00", "currency": "USD"}, proposer=AGENT
    )
    assert result.code == "refused/invalid_arguments"


def test_double_refund_is_refused(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    staged = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "10.00", "currency": "USD"}, proposer=AGENT
    )
    token = gate.grants.list_pending(viewer=OWNER)[0].token
    gate.grants.approve(token=token, verb="ISSUE-REFUND", approver=OWNER)
    gate.executor.run(staged.grant_id)

    second = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "10.00", "currency": "USD"}, proposer=AGENT
    )
    assert second.code == "refused/invalid_arguments"


@pytest.mark.parametrize(
    "args",
    [
        {"ticket": 0},
        {"ticket": "3"},
        {"ticket": 3, "extra": 1},
    ],
)
def test_get_ticket_value_and_shape_refusals(db, frozen_clock, args) -> None:
    gate, _ = _gate(db, frozen_clock)
    result = gate.propose("get_ticket", args, proposer=AGENT)
    assert result.code == "refused/invalid_arguments"


def test_hidden_ticket_is_not_found_for_a_write_tool(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    result = gate.propose("add_internal_note", {"ticket": 7, "text": "x"}, proposer=AGENT)
    assert result.code == "refused/invalid_arguments"


def test_deleted_ticket_is_not_found_once_the_delete_executes(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    staged = gate.propose("delete_ticket", {"ticket": 6}, proposer=AGENT)
    token = gate.grants.list_pending(viewer=OWNER)[0].token
    gate.grants.approve(token=token, verb="DELETE-TICKET", approver=OWNER)
    gate.executor.run(staged.grant_id)

    result = gate.propose("get_ticket", {"ticket": 6}, proposer=AGENT)
    assert result.code == "refused/invalid_arguments"


@pytest.mark.parametrize(
    "amount",
    ["4200", "42.0", "42.001", "0.00"],
)
def test_bad_amount_formats_are_refused(db, frozen_clock, amount) -> None:
    gate, _ = _gate(db, frozen_clock)
    result = gate.propose(
        "issue_refund", {"ticket": 3, "amount": amount, "currency": "USD"}, proposer=AGENT
    )
    assert result.code == "refused/invalid_arguments"


def test_non_usd_currency_is_refused(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    result = gate.propose(
        "issue_refund", {"ticket": 3, "amount": "10.00", "currency": "EUR"}, proposer=AGENT
    )
    assert result.code == "refused/invalid_arguments"


def test_note_text_length_bounds(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    too_long = gate.propose("add_internal_note", {"ticket": 1, "text": "x" * 2001}, proposer=AGENT)
    assert too_long.code == "refused/invalid_arguments"
    empty = gate.propose("add_internal_note", {"ticket": 1, "text": ""}, proposer=AGENT)
    assert empty.code == "refused/invalid_arguments"


def test_derived_tiers_and_verbs_match_the_table(db, frozen_clock) -> None:
    gate, _ = _gate(db, frozen_clock)
    registry = gate._registry
    expected = {
        "list_tickets": (Tier.L1, "READ"),
        "get_ticket": (Tier.L1, "READ"),
        "add_internal_note": (Tier.L2, "ADD-NOTE"),
        "reply_to_customer": (Tier.L4, "SEND-EMAIL"),
        "issue_refund": (Tier.L5, "ISSUE-REFUND"),
        "delete_ticket": (Tier.L5, "DELETE-TICKET"),
    }
    from petasos.trust.risk import derive_tier, verb_for

    for name, (tier, verb) in expected.items():
        definition = registry.get(name)
        assert derive_tier(definition.profile) == tier, name
        assert verb_for(definition.profile) == verb, name
