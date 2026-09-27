"""Gate.propose: signature shape, staging, direct execution, hard constraints, and
proposal refusals (spec 1.2, 1.5, 1.17 to 1.19; acceptance tests 5, 7, 29, 30, 31, 32, 33).
"""

from __future__ import annotations

import inspect
from pathlib import Path

from test_trust_helpers import (
    BROKEN_TOOL,
    EMAIL_TOOL,
    MISMATCHED_MONEY_TOOL,
    NOTE_TOOL,
    REFUND_TOOL,
    make_fake_db,
    seed_ticket,
)

from petasos.ledger import Ledger
from petasos.storage import Database
from petasos.trust import Gate, ToolRegistry
from petasos.trust.risk import RiskProfile, Tier
from petasos.trust.tools import Resolved, ToolDefinition


def _gate(db: Database, clock, approvers=frozenset({"owner"}), scope="s1") -> Gate:
    registry = ToolRegistry([REFUND_TOOL, EMAIL_TOOL, NOTE_TOOL, BROKEN_TOOL])
    return Gate(db, registry, approvers=approvers, scope=scope, clock=clock)


def test_propose_signature_has_no_risk_carrying_parameter() -> None:
    params = list(inspect.signature(Gate.propose).parameters)
    assert params[1:] == ["tool", "arguments", "proposer"]
    for name in params:
        assert "risk" not in name
        assert "tier" not in name
        assert "verb" not in name
        assert "flag" not in name
        assert "approver" not in name or name == "proposer"


def test_executor_run_signature_has_only_grant_id() -> None:
    from petasos.trust.executor import Executor

    params = list(inspect.signature(Executor.run).parameters)
    assert params == ["self", "grant_id"]


def test_l5_refund_stages_with_no_token(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42, version=3, customer="cust-1")
    gate = _gate(db, frozen_clock)

    result = gate.propose(
        "issue_refund", {"ticket": 42, "amount": "42.00", "currency": "USD"}, proposer="agent"
    )
    assert result.code == "staged"
    assert result.tier == Tier.L5

    proposer_view = gate.grants.list_pending(viewer="agent")
    assert len(proposer_view) == 1
    assert proposer_view[0].token is None

    owner_view = gate.grants.list_pending(viewer="owner")
    assert len(owner_view) == 1
    assert owner_view[0].token is not None
    assert owner_view[0].record.amount_minor == 4200


def test_l2_direct_action_executes_at_once(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 7)
    gate = _gate(db, frozen_clock)

    result = gate.propose(
        "add_internal_note", {"ticket": 7, "body": "called back"}, proposer="agent"
    )
    assert result.code == "executed"
    assert result.tier == Tier.L2

    conn = db.connect()
    try:
        notes = conn.execute("SELECT * FROM fake_notes").fetchall()
        rail_rows = conn.execute("SELECT * FROM rail_slots WHERE family='internal'").fetchall()
    finally:
        conn.close()
    assert len(notes) == 1
    assert len(rail_rows) == 1
    assert Ledger(db).verify() is None


def test_disable_gate_is_hard_constrained_even_when_unregistered(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    gate = _gate(db, frozen_clock)

    result = gate.propose("disable_gate", {"anything": "goes", "here": 1}, proposer="agent")
    assert result.code == "refused/hard_constraint"


def test_resolve_to_ledger_resource_is_hard_constrained(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)

    def resolve_to_ledger(conn, validated):
        return Resolved(
            destination=None,
            amount_minor=None,
            currency=None,
            resource="ledger:1",
            resource_version=1,
        )

    def current_version(conn, resource):
        return 1

    sneaky = ToolDefinition(
        name="peek_ledger",
        profile=RiskProfile(mutation="none", reversible=True, touches_money=False, deletes=False),
        validate=lambda args: args,
        resolve=resolve_to_ledger,
        effect=lambda conn, record, key: "noop",
        current_version=current_version,
    )
    registry = ToolRegistry([sneaky])
    gate = Gate(db, registry, approvers=frozenset({"owner"}), scope="s1", clock=frozen_clock)

    result = gate.propose("peek_ledger", {}, proposer="agent")
    assert result.code == "refused/hard_constraint"


def test_unknown_tool_is_refused_and_not_named_in_the_ledger(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    gate = _gate(db, frozen_clock)

    result = gate.propose("no_such_tool_xyz", {"a": 1}, proposer="agent")
    assert result.code == "refused/unknown_tool"

    conn = db.connect()
    try:
        rows = conn.execute("SELECT detail_json FROM ledger").fetchall()
    finally:
        conn.close()
    assert all("no_such_tool_xyz" not in row["detail_json"] for row in rows)


def test_validate_raising_invalid_arguments_is_refused(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    gate = _gate(db, frozen_clock)
    result = gate.propose("issue_refund", {"ticket": "not-an-int"}, proposer="agent")
    assert result.code == "refused/invalid_arguments"


def test_resolve_raising_not_found_is_refused(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    gate = _gate(db, frozen_clock)
    result = gate.propose(
        "issue_refund", {"ticket": 999, "amount": "1.00", "currency": "USD"}, proposer="agent"
    )
    assert result.code == "refused/invalid_arguments"


def test_resolve_raising_any_exception_is_refused(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    gate = _gate(db, frozen_clock)
    result = gate.propose("broken_tool", {"ticket": 1, "body": "x"}, proposer="agent")
    assert result.code == "refused/invalid_arguments"


def test_resolved_money_missing_currency_is_refused_and_ledger_records_it(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 1, version=1, customer="cust-1")
    registry = ToolRegistry([MISMATCHED_MONEY_TOOL])
    gate = Gate(db, registry, approvers=frozenset({"owner"}), scope="s1", clock=frozen_clock)

    result = gate.propose("mismatched_money_tool", {"ticket": 1}, proposer="agent")
    assert result.code == "refused/invalid_arguments"

    conn = db.connect()
    try:
        rows = conn.execute("SELECT kind, detail_json FROM ledger").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["kind"] == "refused"
    assert "invalid_arguments" in rows[0]["detail_json"]
