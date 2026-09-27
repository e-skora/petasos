"""GrantStore: staging, approval, expiry, rails, and abort (spec 1.10 to 1.15, 1.21;
acceptance tests 8 to 18, 28).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from test_trust_helpers import EMAIL_TOOL, NOTE_TOOL, REFUND_TOOL, make_fake_db, seed_ticket

from petasos.trust import Gate, ToolRegistry


def _gate(db, clock, approvers=frozenset({"owner"}), scope="s1") -> Gate:
    registry = ToolRegistry([REFUND_TOOL, EMAIL_TOOL, NOTE_TOOL])
    return Gate(db, registry, approvers=approvers, scope=scope, clock=clock)


def _stage_refund(gate: Gate, ticket: int = 42, proposer: str = "agent"):
    return gate.propose(
        "issue_refund", {"ticket": ticket, "amount": "42.00", "currency": "USD"}, proposer=proposer
    )


def _stage_email(gate: Gate, ticket: int, proposer: str = "agent"):
    return gate.propose("reply_to_customer", {"ticket": ticket, "body": "hello"}, proposer=proposer)


def _run_concurrently(*functions):
    barrier = threading.Barrier(len(functions))
    results: list = [None] * len(functions)
    errors: list = [None] * len(functions)

    def worker(index, fn):
        barrier.wait()
        try:
            results[index] = fn()
        except Exception as exc:  # noqa: BLE001 - surfaced to the test
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


def test_two_threads_approving_same_token_at_once(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token

    r1, r2 = _run_concurrently(
        lambda: gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner"),
        lambda: gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner"),
    )
    codes = sorted([r1.code, r2.code])
    assert codes == ["approved", "refused/unknown_or_used_or_expired"]


def test_right_token_wrong_verb_burns_the_grant(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token

    result = gate.grants.approve(token=token, verb="SEND-EMAIL", approver="owner")
    assert result.code == "refused/verb_mismatch"

    retry = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner")
    assert retry.code == "refused/unknown_or_used_or_expired"


def test_race_between_right_and_wrong_verb(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token

    r1, r2 = _run_concurrently(
        lambda: gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner"),
        lambda: gate.grants.approve(token=token, verb="SEND-EMAIL", approver="owner"),
    )
    codes = sorted([r1.code, r2.code])
    # Whichever call's transaction commits first decides the grant's fate (APPROVED
    # or REJECTED/verb_mismatch); the other call finds the grant no longer AWAITING
    # and falls through to the generic refusal. Exactly one of the two settling
    # outcomes happens, matching spec 1.12's decision tree read fresh each call.
    assert "refused/unknown_or_used_or_expired" in codes
    other = next(c for c in codes if c != "refused/unknown_or_used_or_expired")
    assert other in ("approved", "refused/verb_mismatch")

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM ledger WHERE grant_id=? AND kind IN ('approved', 'rejected')",
            (staged.grant_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_non_approver_is_refused_and_grant_survives(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token

    result = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="intruder")
    assert result.code == "refused/not_an_approver"

    ok = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner")
    assert ok.code == "approved"


def test_self_approval_is_refused_and_grant_survives(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock, approvers=frozenset({"owner", "agent"}))
    _stage_refund(gate, proposer="agent")
    token = gate.grants.list_pending(viewer="owner")[0].token

    result = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="agent")
    assert result.code == "refused/self_approval"

    ok = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner")
    assert ok.code == "approved"


def test_grant_expires_at_the_boundary_instant(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token

    frozen_clock.advance(24 * 3600 - 1)
    assert gate.grants.list_pending(viewer="owner")[0].state == "AWAITING"
    ok = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner")
    assert ok.code == "approved"


def test_grant_is_expired_exactly_at_ttl(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token

    frozen_clock.advance(24 * 3600)
    pending = gate.grants.list_pending(viewer="owner")
    assert pending == []
    refused = gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner")
    assert refused.code == "refused/unknown_or_used_or_expired"


def test_approved_grant_past_expiry_is_refused_by_executor(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 42)
    gate = _gate(db, frozen_clock)
    staged = _stage_refund(gate)
    token = gate.grants.list_pending(viewer="owner")[0].token
    gate.grants.approve(token=token, verb="ISSUE-REFUND", approver="owner")

    frozen_clock.advance(24 * 3600)
    result = gate.executor.run(staged.grant_id)
    assert result.code == "refused/not_approved"


def test_sixth_email_staging_in_one_hour_is_rail_full(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 7):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    results = [_stage_email(gate, i) for i in range(1, 6)]
    assert all(r.code == "staged" for r in results)

    sixth = _stage_email(gate, 6)
    assert sixth.code == "refused/rail_full"

    note_result = gate.propose(
        "add_internal_note", {"ticket": 1, "body": "still fine"}, proposer="agent"
    )
    assert note_result.code == "executed"


def test_all_five_staged_emails_can_be_approved_and_executed(
    tmp_sqlite: Path, frozen_clock
) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 6):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    staged = [_stage_email(gate, i) for i in range(1, 6)]
    for s in staged:
        card = next(g for g in gate.grants.list_pending(viewer="owner") if g.id == s.grant_id)
        approved = gate.grants.approve(token=card.token, verb="SEND-EMAIL", approver="owner")
        assert approved.code == "approved"
        run_result = gate.executor.run(s.grant_id)
        assert run_result.code == "executed"


def test_aborting_frees_a_rail_slot(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 7):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    staged = [_stage_email(gate, i) for i in range(1, 6)]
    assert all(r.code == "staged" for r in staged)
    assert _stage_email(gate, 6).code == "refused/rail_full"

    card = gate.grants.list_pending(viewer="owner")[0]
    aborted = gate.grants.abort(token=card.token, approver="owner")
    assert aborted.code == "aborted"

    sixth = _stage_email(gate, 6)
    assert sixth.code == "staged"


def test_verb_mismatch_burn_frees_a_rail_slot(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 7):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    staged = [_stage_email(gate, i) for i in range(1, 6)]
    assert all(r.code == "staged" for r in staged)

    card = gate.grants.list_pending(viewer="owner")[0]
    burned = gate.grants.approve(token=card.token, verb="ISSUE-REFUND", approver="owner")
    assert burned.code == "refused/verb_mismatch"

    sixth = _stage_email(gate, 6)
    assert sixth.code == "staged"


def test_stale_resource_rejection_frees_a_rail_slot(tmp_sqlite: Path, frozen_clock) -> None:
    from test_trust_helpers import bump_ticket_version

    db = make_fake_db(tmp_sqlite)
    for i in range(1, 7):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    staged = [_stage_email(gate, i) for i in range(1, 6)]
    assert all(r.code == "staged" for r in staged)

    target = staged[0]
    card = next(g for g in gate.grants.list_pending(viewer="owner") if g.id == target.grant_id)
    gate.grants.approve(token=card.token, verb="SEND-EMAIL", approver="owner")
    bump_ticket_version(db, 1)
    stale = gate.executor.run(target.grant_id)
    assert stale.code == "refused/stale_resource"

    sixth = _stage_email(gate, 6)
    assert sixth.code == "staged"


def test_two_threads_staging_the_last_free_slot(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 3):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    # Fill 4 of the 5 external-family slots directly, leaving exactly one free.
    for _ in range(4):
        assert _stage_email(gate, 1).code == "staged"

    r1, r2 = _run_concurrently(
        lambda: _stage_email(gate, 1),
        lambda: _stage_email(gate, 2),
    )
    codes = sorted([r1.code, r2.code])
    assert codes == ["refused/rail_full", "staged"]


def test_a_token_is_unique_across_every_grant(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    seed_ticket(db, 1)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO grants (scope, token, verb, tier, state, proposer, staged_at, "
            "expires_at, record_json, record_hash) "
            "VALUES ('s1', 'dupe-token', 'SEND-EMAIL', 'L4', 'AWAITING', 'agent', "
            "'2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', '{}', 'h1')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO grants (scope, token, verb, tier, state, proposer, staged_at, "
                "expires_at, record_json, record_hash) "
                "VALUES ('s2', 'dupe-token', 'ISSUE-REFUND', 'L5', 'AWAITING', 'agent', "
                "'2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', '{}', 'h2')"
            )
    finally:
        conn.close()


def test_abort_all_aborts_every_live_grant_in_scope_only(tmp_sqlite: Path, frozen_clock) -> None:
    db = make_fake_db(tmp_sqlite)
    for i in range(1, 4):
        seed_ticket(db, i)
    gate = _gate(db, frozen_clock)

    staged = [_stage_email(gate, i) for i in (1, 2)]
    assert all(r.code == "staged" for r in staged)

    denied = gate.grants.abort_all(approver="agent")
    assert denied.code == "refused/not_an_approver"
    assert len(gate.grants.list_pending(viewer="owner")) == 2

    ok = gate.grants.abort_all(approver="owner")
    assert ok.code == "aborted"
    assert gate.grants.list_pending(viewer="owner") == []
