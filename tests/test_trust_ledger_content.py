"""No ledger row written while exercising the gate contains an argument value, a
destination, or an unknown tool name (spec 1.22; acceptance test 36). The record hash
stands in for the content instead.
"""

from __future__ import annotations

from pathlib import Path

from test_trust_helpers import EMAIL_TOOL, NOTE_TOOL, REFUND_TOOL, make_fake_db, seed_ticket

from petasos.trust import Gate, ToolRegistry

SECRET_BODY = "the customer's secret complaint about their neighbor's lawnmower"
SECRET_DESTINATION_TICKET = 7
UNKNOWN_TOOL_NAME = "definitely_not_a_registered_tool_marker"
SENSITIVE_STRINGS = [SECRET_BODY, UNKNOWN_TOOL_NAME, "cust-marker-42"]


def test_ledger_never_holds_caller_supplied_text(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 1, customer="cust-marker-42")
    seed_ticket(db, SECRET_DESTINATION_TICKET, customer="cust-marker-42")

    registry = ToolRegistry([REFUND_TOOL, EMAIL_TOOL, NOTE_TOOL])
    gate = Gate(db, registry, approvers=frozenset({"owner"}), scope="s1", clock=frozen_clock)

    # A staged, approved, and executed action.
    staged = gate.propose(
        "issue_refund", {"ticket": 1, "amount": "10.00", "currency": "USD"}, proposer="agent"
    )
    card = gate.grants.list_pending(viewer="owner")[0]
    gate.grants.approve(token=card.token, verb="ISSUE-REFUND", approver="owner")
    gate.executor.run(staged.grant_id)

    # A staged action carrying a secret note body, then aborted.
    gate.propose(
        "reply_to_customer",
        {"ticket": SECRET_DESTINATION_TICKET, "body": SECRET_BODY},
        proposer="agent",
    )
    note_card = gate.grants.list_pending(viewer="owner")[0]
    gate.grants.abort(token=note_card.token, approver="owner")

    # A direct action carrying the secret body too.
    gate.propose("add_internal_note", {"ticket": 1, "body": SECRET_BODY}, proposer="agent")

    # Refusals: unknown tool, invalid arguments, hard constraint.
    gate.propose(UNKNOWN_TOOL_NAME, {"body": SECRET_BODY}, proposer="agent")
    gate.propose("issue_refund", {"ticket": "not-a-number"}, proposer="agent")
    gate.propose("disable_gate", {"body": SECRET_BODY}, proposer="agent")

    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM ledger").fetchall()
    finally:
        conn.close()

    assert len(rows) > 0
    text_columns = ("kind", "scope", "actor", "verb", "tier", "detail_json")
    for row in rows:
        for column in text_columns:
            value = row[column]
            if value is None:
                continue
            for sensitive in SENSITIVE_STRINGS:
                assert sensitive not in value, (
                    f"{sensitive!r} leaked into ledger column {column!r}: {value!r}"
                )
