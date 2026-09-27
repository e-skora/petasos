"""Scope isolation: one visitor's owner can never see, approve, abort, or run another
visitor's work; rails are an admission limit over a rolling hour, not a cap on total
grants in flight (spec 1.13, 1.26; acceptance tests 39, 40).
"""

from __future__ import annotations

from pathlib import Path

from test_trust_helpers import EMAIL_TOOL, REFUND_TOOL, make_fake_db, seed_ticket

from petasos.trust import Gate, ToolRegistry


def _gate(db, clock, scope) -> Gate:
    registry = ToolRegistry([REFUND_TOOL, EMAIL_TOOL])
    return Gate(db, registry, approvers=frozenset({"owner"}), scope=scope, clock=clock)


def test_scopes_are_fully_isolated(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 1)
    seed_ticket(db, 2)
    gate_s1 = _gate(db, frozen_clock, "s1")
    gate_s2 = _gate(db, frozen_clock, "s2")

    staged_s1 = gate_s1.propose(
        "issue_refund", {"ticket": 1, "amount": "10.00", "currency": "USD"}, proposer="agent"
    )
    assert staged_s1.code == "staged"
    token_s1 = gate_s1.grants.list_pending(viewer="owner")[0].token

    assert gate_s2.grants.list_pending(viewer="owner") == []
    assert gate_s2.grants.approve(token=token_s1, verb="ISSUE-REFUND", approver="owner").code == (
        "refused/unknown_or_used_or_expired"
    )
    assert (
        gate_s2.grants.abort(token=token_s1, approver="owner").code
        == "refused/unknown_or_used_or_expired"
    )
    assert gate_s2.executor.run(staged_s1.grant_id).code == "refused/not_approved"

    still_pending = gate_s1.grants.list_pending(viewer="owner")
    assert len(still_pending) == 1
    assert still_pending[0].token == token_s1


def test_abort_all_in_one_scope_leaves_the_other_untouched(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 1)
    seed_ticket(db, 2)
    gate_s1 = _gate(db, frozen_clock, "s1")
    gate_s2 = _gate(db, frozen_clock, "s2")

    gate_s1.propose(
        "issue_refund", {"ticket": 1, "amount": "10.00", "currency": "USD"}, proposer="agent"
    )
    gate_s2.propose(
        "issue_refund", {"ticket": 2, "amount": "20.00", "currency": "USD"}, proposer="agent"
    )

    gate_s2.grants.abort_all(approver="owner")

    assert gate_s2.grants.list_pending(viewer="owner") == []
    assert len(gate_s1.grants.list_pending(viewer="owner")) == 1


def test_rails_are_counted_per_scope(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 6):
        seed_ticket(db, i)
    gate_s1 = _gate(db, frozen_clock, "s1")
    gate_s2 = _gate(db, frozen_clock, "s2")

    for i in range(1, 6):
        result = gate_s1.propose("reply_to_customer", {"ticket": i, "body": "hi"}, proposer="agent")
        assert result.code == "staged"

    # s1's rail is now full, but s2's own rail is untouched.
    seed_ticket(db, 6)
    result = gate_s2.propose("reply_to_customer", {"ticket": 6, "body": "hi"}, proposer="agent")
    assert result.code == "staged"


def test_every_ledger_row_carries_its_scope(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 1)
    gate_s1 = _gate(db, frozen_clock, "s1")
    gate_s1.propose(
        "issue_refund", {"ticket": 1, "amount": "10.00", "currency": "USD"}, proposer="agent"
    )

    conn = db.connect()
    try:
        rows = conn.execute("SELECT scope FROM ledger").fetchall()
    finally:
        conn.close()
    assert len(rows) > 0
    assert all(row["scope"] == "s1" for row in rows)


def test_rail_admission_limit_is_a_rolling_hour(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 7):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock, "s1")

    for i in range(1, 6):
        result = gate.propose("reply_to_customer", {"ticket": i, "body": "hi"}, proposer="agent")
        assert result.code == "staged"

    sixth = gate.propose("reply_to_customer", {"ticket": 6, "body": "hi"}, proposer="agent")
    assert sixth.code == "refused/rail_full"

    frozen_clock.advance(61 * 60)

    # The first five grants are still live (24h TTL), but the rail's 1-hour window
    # has rolled past their reservations, so a new staging is admitted.
    still_live = gate.grants.list_pending(viewer="owner")
    assert len(still_live) == 5

    seventh = gate.propose("reply_to_customer", {"ticket": 6, "body": "hi again"}, proposer="agent")
    assert seventh.code == "staged"
