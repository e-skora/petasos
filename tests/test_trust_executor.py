"""Executor.run: one transaction for the effect, the grant transition, and the ledger
row; refusals for a mismatched, stale, or already-run grant (spec 1.16; acceptance
tests 19 to 27).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from hashlib import sha256
from pathlib import Path

from test_trust_helpers import (
    REFUND_TOOL,
    bump_ticket_version,
    make_fake_db,
    seed_ticket,
    ticket_version,
)

from petasos.trust import Gate, ToolRegistry
from petasos.trust.record import canonical_json
from petasos.trust.tools import ToolDefinition


def _gate(db, clock, approvers=frozenset({"owner"}), scope="s1", registry=None):
    registry = registry or ToolRegistry([REFUND_TOOL])
    return Gate(db, registry, approvers=approvers, scope=scope, clock=clock)


def _stage_and_approve(gate, ticket=42, proposer="agent", approver="owner"):
    staged = gate.propose(
        "issue_refund", {"ticket": ticket, "amount": "42.00", "currency": "USD"}, proposer=proposer
    )
    card = gate.grants.list_pending(viewer=approver)[0]
    gate.grants.approve(token=card.token, verb="ISSUE-REFUND", approver=approver)
    return staged


def _run_concurrently(*functions):
    barrier = threading.Barrier(len(functions))
    results: list = [None] * len(functions)
    errors: list = [None] * len(functions)

    def worker(index, fn):
        barrier.wait()
        try:
            results[index] = fn()
        except Exception as exc:  # noqa: BLE001
            errors[index] = exc

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(functions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for err in errors:
        if err is not None:
            raise err
    return results


def test_approve_then_run_commits_effect_grant_and_ledger_together(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)

    def effect(conn: sqlite3.Connection, record, idempotency_key: str) -> str:
        assert conn.in_transaction is True
        conn.execute(
            "INSERT INTO fake_refunds (idempotency_key, ticket_id, amount_minor, currency) VALUES (?,?,?,?)",
            (idempotency_key, 42, record.amount_minor, record.currency),
        )
        return "refund_recorded"

    tool = ToolDefinition(
        name="issue_refund",
        profile=REFUND_TOOL.profile,
        validate=REFUND_TOOL.validate,
        resolve=REFUND_TOOL.resolve,
        effect=effect,
        current_version=ticket_version,
    )
    gate = _gate(db, frozen_clock, registry=ToolRegistry([tool]))
    staged = _stage_and_approve(gate)

    result = gate.executor.run(staged.grant_id)
    assert result.code == "executed"

    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
        grant = conn.execute("SELECT state FROM grants WHERE id=?", (staged.grant_id,)).fetchone()
        ledger_rows = conn.execute(
            "SELECT * FROM ledger WHERE grant_id=? AND kind='executed'", (staged.grant_id,)
        ).fetchall()
    finally:
        conn.close()
    assert len(refunds) == 1
    assert grant["state"] == "EXECUTED"
    assert len(ledger_rows) == 1


def test_running_an_executed_grant_again_is_a_no_op(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_and_approve(gate)

    first = gate.executor.run(staged.grant_id)
    assert first.code == "executed"
    second = gate.executor.run(staged.grant_id)
    assert second.code == "already_executed"

    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
    finally:
        conn.close()
    assert len(refunds) == 1


def test_two_threads_running_same_grant_effect_runs_once(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_and_approve(gate)

    r1, r2 = _run_concurrently(
        lambda: gate.executor.run(staged.grant_id),
        lambda: gate.executor.run(staged.grant_id),
    )
    codes = sorted([r1.code, r2.code])
    assert codes == ["already_executed", "executed"]

    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
    finally:
        conn.close()
    assert len(refunds) == 1


def test_effect_raising_after_writing_rolls_back_and_can_retry(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    call_count = {"n": 0}

    def flaky_effect(conn: sqlite3.Connection, record, idempotency_key: str) -> str:
        call_count["n"] += 1
        conn.execute(
            "INSERT INTO fake_refunds (idempotency_key, ticket_id, amount_minor, currency) VALUES (?,?,?,?)",
            (idempotency_key, 42, record.amount_minor, record.currency),
        )
        if call_count["n"] == 1:
            raise RuntimeError("simulated payment provider failure")
        return "refund_recorded"

    tool = ToolDefinition(
        name="issue_refund",
        profile=REFUND_TOOL.profile,
        validate=REFUND_TOOL.validate,
        resolve=REFUND_TOOL.resolve,
        effect=flaky_effect,
        current_version=ticket_version,
    )
    gate = _gate(db, frozen_clock, registry=ToolRegistry([tool]))
    staged = _stage_and_approve(gate)

    failed = gate.executor.run(staged.grant_id)
    assert failed.code == "failed/execution_error"

    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
        grant = conn.execute("SELECT state FROM grants WHERE id=?", (staged.grant_id,)).fetchone()
        failed_rows = conn.execute(
            "SELECT * FROM ledger WHERE grant_id=? AND kind='failed'", (staged.grant_id,)
        ).fetchall()
    finally:
        conn.close()
    assert refunds == []
    assert grant["state"] == "APPROVED"
    assert len(failed_rows) == 1

    retried = gate.executor.run(staged.grant_id)
    assert retried.code == "executed"
    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
    finally:
        conn.close()
    assert len(refunds) == 1


def test_ledger_append_raising_rolls_back_effect_and_grant(
    tmp_sqlite: Path, frozen_clock, monkeypatch
) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_and_approve(gate)

    import petasos.trust.executor as executor_module

    real_append = executor_module.ledger_append

    def boom_on_executed(conn, *, kind, **kwargs):
        if kind == "executed":
            raise RuntimeError("simulated ledger failure")
        return real_append(conn, kind=kind, **kwargs)

    monkeypatch.setattr(executor_module, "ledger_append", boom_on_executed)

    result = gate.executor.run(staged.grant_id)
    assert result.code == "failed/execution_error"

    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
        grant = conn.execute("SELECT state FROM grants WHERE id=?", (staged.grant_id,)).fetchone()
    finally:
        conn.close()
    assert refunds == []
    assert grant["state"] == "APPROVED"


def _tamper_record_field(db, grant_id: int, field: str, value) -> None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT record_json FROM grants WHERE id=?", (grant_id,)).fetchone()
        record_dict = json.loads(row["record_json"])
        record_dict[field] = value
        conn.execute(
            "UPDATE grants SET record_json=? WHERE id=?",
            (canonical_json(record_dict).decode("utf-8"), grant_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_editing_any_record_field_causes_record_mismatch(tmp_sqlite: Path, frozen_clock) -> None:
    fields_and_values = {
        "tool": "delete_ticket",
        "arguments": {"ticket": 99, "amount_minor": 1, "currency": "USD"},
        "destination": "someone-else",
        "amount_minor": 999,
        "currency": "EUR",
        "resource": "ticket:99",
        "resource_version": 999,
        "proposer": "someone-else",
        "policy_version": "999",
        "scope": "other-scope",
    }
    for field, value in fields_and_values.items():
        db = make_fake_db(tmp_sqlite.with_name(f"{field}.sqlite"))
        seed_ticket(db, 42)
        gate = _gate(db, frozen_clock)
        staged = _stage_and_approve(gate)

        _tamper_record_field(db, staged.grant_id, field, value)

        result = gate.executor.run(staged.grant_id)
        assert result.code == "refused/record_mismatch", (
            f"field {field!r} did not trigger record_mismatch"
        )

        conn = db.connect()
        try:
            refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
        finally:
            conn.close()
        assert refunds == []


def test_stale_resource_after_approval_rejects_and_releases_slot(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_and_approve(gate)

    bump_ticket_version(db, 42)
    result = gate.executor.run(staged.grant_id)
    assert result.code == "refused/stale_resource"

    conn = db.connect()
    try:
        grant = conn.execute(
            "SELECT state, reason FROM grants WHERE id=?", (staged.grant_id,)
        ).fetchone()
        slot = conn.execute(
            "SELECT released_at FROM rail_slots WHERE grant_id=?", (staged.grant_id,)
        ).fetchone()
    finally:
        conn.close()
    assert grant["state"] == "REJECTED"
    assert grant["reason"] == "stale_resource"
    assert slot["released_at"] is not None


def test_stale_policy_version_is_refused(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_and_approve(gate)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT record_json FROM grants WHERE id=?", (staged.grant_id,)
        ).fetchone()
        record_dict = json.loads(row["record_json"])
        record_dict["policy_version"] = "999"
        new_json = canonical_json(record_dict).decode("utf-8")
        new_hash = sha256(canonical_json(record_dict)).hexdigest()
        conn.execute(
            "UPDATE grants SET record_json=?, record_hash=? WHERE id=?",
            (new_json, new_hash, staged.grant_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = gate.executor.run(staged.grant_id)
    assert result.code == "refused/stale_policy"

    conn = db.connect()
    try:
        grant = conn.execute(
            "SELECT state, reason FROM grants WHERE id=?", (staged.grant_id,)
        ).fetchone()
    finally:
        conn.close()
    assert grant["state"] == "REJECTED"
    assert grant["reason"] == "stale_policy"


def test_abort_and_run_race_exactly_one_wins(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_and_approve(gate)
    # The grant is already approved, so it will not show up as pending; fetch its token
    # from the grants table directly for the abort call.
    conn = db.connect()
    try:
        token = conn.execute("SELECT token FROM grants WHERE id=?", (staged.grant_id,)).fetchone()[
            "token"
        ]
    finally:
        conn.close()

    r1, r2 = _run_concurrently(
        lambda: gate.grants.abort(token=token, approver="owner"),
        lambda: gate.executor.run(staged.grant_id),
    )
    codes = sorted([r1.code, r2.code])
    # Whichever call's transaction commits first wins: abort first leaves the run
    # call seeing a non-APPROVED grant (refused/not_approved); run first leaves the
    # abort call seeing a grant that is no longer AWAITING/APPROVED, which is
    # indistinguishable from an unknown token (refused/unknown_or_used_or_expired).
    assert codes in (
        ["aborted", "refused/not_approved"],
        ["executed", "refused/unknown_or_used_or_expired"],
    )

    conn = db.connect()
    try:
        refunds = conn.execute("SELECT * FROM fake_refunds").fetchall()
        grant = conn.execute("SELECT state FROM grants WHERE id=?", (staged.grant_id,)).fetchone()
    finally:
        conn.close()
    assert len(refunds) <= 1
    assert grant["state"] in ("ABORTED", "EXECUTED")
