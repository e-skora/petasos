"""GrantStore: stage, list (with an expiry sweep), approve (compare-and-swap), abort,
burn on verb mismatch, refuse self-approval, and hourly rails that reserve at staging
and release on abort or rejection (spec 1.10 to 1.15, 1.21, 1.26).
"""

from __future__ import annotations

import dataclasses
import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from petasos.ledger.chain import append as ledger_append
from petasos.trust.outcomes import Result, make_result
from petasos.trust.record import ActionRecord, canonical_json, record_as_dict
from petasos.trust.risk import Tier, rail_for

if TYPE_CHECKING:
    from petasos.storage import Database

GRANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    token TEXT UNIQUE,
    verb TEXT NOT NULL,
    tier TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    proposer TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    executed_at TEXT,
    outcome_code TEXT,
    staged_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
"""

RAIL_SLOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rail_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    family TEXT NOT NULL,
    grant_id INTEGER,
    reserved_at TEXT NOT NULL,
    released_at TEXT
);
"""

GRANT_TTL = timedelta(hours=24)
RAIL_WINDOW = timedelta(hours=1)


def rail_available(
    conn: sqlite3.Connection, *, scope: str, family: str, cap: int, now: datetime
) -> bool:
    window_start = (now - RAIL_WINDOW).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM rail_slots "
        "WHERE scope=? AND family=? AND reserved_at>? AND released_at IS NULL",
        (scope, family, window_start),
    ).fetchone()
    return row["n"] < cap


def reserve_slot(
    conn: sqlite3.Connection, *, scope: str, family: str, grant_id: int | None, now: datetime
) -> None:
    conn.execute(
        "INSERT INTO rail_slots (scope, family, grant_id, reserved_at, released_at) "
        "VALUES (?, ?, ?, ?, NULL)",
        (scope, family, grant_id, now.isoformat()),
    )


def release_slot_for_grant(conn: sqlite3.Connection, *, grant_id: int, now: datetime) -> None:
    conn.execute(
        "UPDATE rail_slots SET released_at=? WHERE grant_id=? AND released_at IS NULL",
        (now.isoformat(), grant_id),
    )


def sweep(conn: sqlite3.Connection, *, scope: str, now: datetime) -> None:
    """Move every AWAITING or APPROVED grant in `scope` whose `expires_at` has passed
    to EXPIRED, releasing its rail slot and appending one `expired` ledger row each.
    The boundary instant (`expires_at == now`) counts as expired."""
    now_s = now.isoformat()
    rows = conn.execute(
        "SELECT * FROM grants WHERE scope=? AND state IN ('AWAITING', 'APPROVED') AND expires_at<=?",
        (scope, now_s),
    ).fetchall()
    for row in rows:
        conn.execute("UPDATE grants SET state='EXPIRED' WHERE id=?", (row["id"],))
        release_slot_for_grant(conn, grant_id=row["id"], now=now)
        ledger_append(
            conn,
            now=now,
            kind="expired",
            scope=scope,
            actor=row["proposer"],
            verb=row["verb"],
            tier=row["tier"],
            grant_id=row["id"],
            record_hash=row["record_hash"],
            detail={"code": "expired"},
        )


@dataclasses.dataclass(frozen=True)
class Grant:
    id: int
    scope: str
    token: str | None
    verb: str
    tier: Tier
    state: str
    reason: str | None
    proposer: str
    approved_by: str | None
    record: ActionRecord
    record_hash: str
    expires_at: datetime

    def for_proposer(self) -> Grant:
        return dataclasses.replace(self, token=None)

    def for_approver(self) -> Grant:
        return self


def _row_to_grant(row: sqlite3.Row) -> Grant:
    record = ActionRecord(**json.loads(row["record_json"]))
    return Grant(
        id=row["id"],
        scope=row["scope"],
        token=row["token"],
        verb=row["verb"],
        tier=Tier(row["tier"]),
        state=row["state"],
        reason=row["reason"],
        proposer=row["proposer"],
        approved_by=row["approved_by"],
        record=record,
        record_hash=row["record_hash"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


class GrantStore:
    def __init__(
        self,
        db: Database,
        *,
        approvers: frozenset[str],
        scope: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._approvers = approvers
        self._scope = scope
        self._clock = clock

    def stage(
        self,
        *,
        record: ActionRecord,
        record_hash_value: str,
        tier: Tier,
        verb: str,
        actor: str,
        now: datetime,
    ) -> Result:
        rail = rail_for(verb)
        with self._db.transaction() as conn:
            if rail is not None:
                family, cap = rail
                if not rail_available(conn, scope=self._scope, family=family, cap=cap, now=now):
                    ledger_append(
                        conn,
                        now=now,
                        kind="refused",
                        scope=self._scope,
                        actor=actor,
                        verb=verb,
                        tier=tier.value,
                        grant_id=None,
                        record_hash=None,
                        detail={"code": "rail_full"},
                    )
                    return make_result("refused/rail_full", tier=tier, verb=verb)

            token = secrets.token_urlsafe(16)
            expires_at = now + GRANT_TTL
            record_json = canonical_json(record_as_dict(record)).decode("utf-8")
            cur = conn.execute(
                "INSERT INTO grants "
                "(scope, token, verb, tier, state, proposer, staged_at, expires_at, "
                " record_json, record_hash) "
                "VALUES (?, ?, ?, ?, 'AWAITING', ?, ?, ?, ?, ?)",
                (
                    self._scope,
                    token,
                    verb,
                    tier.value,
                    record.proposer,
                    now.isoformat(),
                    expires_at.isoformat(),
                    record_json,
                    record_hash_value,
                ),
            )
            grant_id = cur.lastrowid
            if rail is not None:
                family, cap = rail
                reserve_slot(conn, scope=self._scope, family=family, grant_id=grant_id, now=now)
            ledger_append(
                conn,
                now=now,
                kind="staged",
                scope=self._scope,
                actor=actor,
                verb=verb,
                tier=tier.value,
                grant_id=grant_id,
                record_hash=record_hash_value,
                detail={"code": "staged"},
            )
            return make_result("staged", tier=tier, verb=verb, grant_id=grant_id, scope=self._scope)

    def list_pending(self, viewer: str) -> list[Grant]:
        now = self._clock()
        with self._db.transaction() as conn:
            sweep(conn, scope=self._scope, now=now)
            rows = conn.execute(
                "SELECT * FROM grants WHERE scope=? AND state='AWAITING' ORDER BY id ASC",
                (self._scope,),
            ).fetchall()
        grants = [_row_to_grant(row) for row in rows]
        if viewer in self._approvers:
            return [g.for_approver() for g in grants]
        return [g.for_proposer() for g in grants]

    def approve(self, *, token: str, verb: str, approver: str) -> Result:
        now = self._clock()
        if approver not in self._approvers:
            return make_result("refused/not_an_approver")

        with self._db.transaction() as conn:
            sweep(conn, scope=self._scope, now=now)
            now_s = now.isoformat()

            cur = conn.execute(
                "UPDATE grants SET state='APPROVED', approved_by=?, approved_at=? "
                "WHERE token=? AND verb=? AND state='AWAITING' AND expires_at>? "
                "AND proposer<>? AND scope=?",
                (approver, now_s, token, verb, now_s, approver, self._scope),
            )
            if cur.rowcount == 1:
                grant = _row_to_grant(
                    conn.execute(
                        "SELECT * FROM grants WHERE token=? AND scope=?", (token, self._scope)
                    ).fetchone()
                )
                ledger_append(
                    conn,
                    now=now,
                    kind="approved",
                    scope=self._scope,
                    actor=approver,
                    verb=grant.verb,
                    tier=grant.tier.value,
                    grant_id=grant.id,
                    record_hash=grant.record_hash,
                    detail={"code": "approved"},
                )
                return make_result(
                    "approved",
                    tier=grant.tier,
                    verb=grant.verb,
                    grant_id=grant.id,
                    scope=self._scope,
                )

            cur2 = conn.execute(
                "UPDATE grants SET state='REJECTED', reason='verb_mismatch' "
                "WHERE token=? AND verb<>? AND state='AWAITING' AND expires_at>? AND scope=?",
                (token, verb, now_s, self._scope),
            )
            if cur2.rowcount == 1:
                grant = _row_to_grant(
                    conn.execute(
                        "SELECT * FROM grants WHERE token=? AND scope=?", (token, self._scope)
                    ).fetchone()
                )
                release_slot_for_grant(conn, grant_id=grant.id, now=now)
                ledger_append(
                    conn,
                    now=now,
                    kind="rejected",
                    scope=self._scope,
                    actor=approver,
                    verb=grant.verb,
                    tier=grant.tier.value,
                    grant_id=grant.id,
                    record_hash=grant.record_hash,
                    detail={"code": "verb_mismatch"},
                )
                return make_result(
                    "refused/verb_mismatch",
                    tier=grant.tier,
                    verb=grant.verb,
                    grant_id=grant.id,
                    scope=self._scope,
                )

            row = conn.execute(
                "SELECT * FROM grants WHERE token=? AND state='AWAITING' AND expires_at>? AND scope=?",
                (token, now_s, self._scope),
            ).fetchone()
            if row is not None and row["proposer"] == approver:
                ledger_append(
                    conn,
                    now=now,
                    kind="refused",
                    scope=self._scope,
                    actor=approver,
                    verb=row["verb"],
                    tier=row["tier"],
                    grant_id=row["id"],
                    record_hash=row["record_hash"],
                    detail={"code": "self_approval"},
                )
                return make_result(
                    "refused/self_approval",
                    tier=Tier(row["tier"]),
                    verb=row["verb"],
                    grant_id=row["id"],
                    scope=self._scope,
                )

            ledger_append(
                conn,
                now=now,
                kind="refused",
                scope=self._scope,
                actor=approver,
                verb=None,
                tier=None,
                grant_id=None,
                record_hash=None,
                detail={"code": "unknown_or_used_or_expired"},
            )
            return make_result("refused/unknown_or_used_or_expired")

    def abort(self, *, token: str, approver: str) -> Result:
        now = self._clock()
        if approver not in self._approvers:
            return make_result("refused/not_an_approver")

        with self._db.transaction() as conn:
            sweep(conn, scope=self._scope, now=now)
            row = conn.execute(
                "SELECT * FROM grants WHERE token=? AND scope=? AND state IN ('AWAITING', 'APPROVED')",
                (token, self._scope),
            ).fetchone()
            if row is None:
                ledger_append(
                    conn,
                    now=now,
                    kind="refused",
                    scope=self._scope,
                    actor=approver,
                    verb=None,
                    tier=None,
                    grant_id=None,
                    record_hash=None,
                    detail={"code": "unknown_or_used_or_expired"},
                )
                return make_result("refused/unknown_or_used_or_expired")

            conn.execute("UPDATE grants SET state='ABORTED' WHERE id=?", (row["id"],))
            release_slot_for_grant(conn, grant_id=row["id"], now=now)
            ledger_append(
                conn,
                now=now,
                kind="aborted",
                scope=self._scope,
                actor=approver,
                verb=row["verb"],
                tier=row["tier"],
                grant_id=row["id"],
                record_hash=row["record_hash"],
                detail={"code": "aborted"},
            )
            return make_result(
                "aborted",
                tier=Tier(row["tier"]),
                verb=row["verb"],
                grant_id=row["id"],
                scope=self._scope,
            )

    def abort_all(self, *, approver: str) -> Result:
        now = self._clock()
        if approver not in self._approvers:
            return make_result("refused/not_an_approver")

        with self._db.transaction() as conn:
            sweep(conn, scope=self._scope, now=now)
            rows = conn.execute(
                "SELECT * FROM grants WHERE scope=? AND state IN ('AWAITING', 'APPROVED')",
                (self._scope,),
            ).fetchall()
            for row in rows:
                conn.execute("UPDATE grants SET state='ABORTED' WHERE id=?", (row["id"],))
                release_slot_for_grant(conn, grant_id=row["id"], now=now)
                ledger_append(
                    conn,
                    now=now,
                    kind="aborted",
                    scope=self._scope,
                    actor=approver,
                    verb=row["verb"],
                    tier=row["tier"],
                    grant_id=row["id"],
                    record_hash=row["record_hash"],
                    detail={"code": "aborted"},
                )
            return make_result("aborted", scope=self._scope)
