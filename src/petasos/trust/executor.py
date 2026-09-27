"""Executor.run(grant_id): one transaction for the effect, the grant change, and the
ledger row (spec 1.16). `run_direct` is the same path for an L1 to L3 action that
never has a grant (spec 1.17).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from petasos.ledger.chain import append as ledger_append
from petasos.trust.grants import rail_available, release_slot_for_grant, reserve_slot, sweep
from petasos.trust.outcomes import Result, make_result
from petasos.trust.record import ActionRecord, canonical_json
from petasos.trust.risk import POLICY_VERSION, Tier, rail_for
from petasos.trust.tools import ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from petasos.storage import Database


class Executor:
    def __init__(
        self,
        db: Database,
        *,
        registry: ToolRegistry,
        scope: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._registry = registry
        self._scope = scope
        self._clock = clock

    def run(self, grant_id: int) -> Result:
        now = self._clock()
        failure_ctx: dict[str, Any] = {
            "actor": "system",
            "verb": None,
            "tier": None,
            "record_hash": None,
        }
        try:
            with self._db.transaction() as conn:
                result = self._execute_locked(conn, grant_id, now, failure_ctx)
            return result
        except Exception:  # noqa: BLE001 - fail closed on any effect or ledger failure (spec 1.16)
            with self._db.transaction() as conn:
                ledger_append(
                    conn,
                    now=now,
                    kind="failed",
                    scope=self._scope,
                    actor=failure_ctx["actor"],
                    verb=failure_ctx["verb"],
                    tier=failure_ctx["tier"],
                    grant_id=grant_id,
                    record_hash=failure_ctx["record_hash"],
                    detail={"code": "execution_error"},
                )
            tier_value = failure_ctx["tier"]
            return make_result(
                "failed/execution_error",
                tier=Tier(tier_value) if tier_value else None,
                verb=failure_ctx["verb"],
                grant_id=grant_id,
                scope=self._scope,
            )

    def _execute_locked(
        self, conn: sqlite3.Connection, grant_id: int, now: datetime, failure_ctx: dict[str, Any]
    ) -> Result:
        sweep(conn, scope=self._scope, now=now)
        row = conn.execute(
            "SELECT * FROM grants WHERE id=? AND scope=?", (grant_id, self._scope)
        ).fetchone()
        if row is None:
            ledger_append(
                conn,
                now=now,
                kind="refused",
                scope=self._scope,
                actor="system",
                verb=None,
                tier=None,
                grant_id=grant_id,
                record_hash=None,
                detail={"code": "not_approved"},
            )
            return make_result("refused/not_approved", grant_id=grant_id, scope=self._scope)

        actor = row["approved_by"] or row["proposer"]
        failure_ctx.update(
            actor=actor, verb=row["verb"], tier=row["tier"], record_hash=row["record_hash"]
        )

        if row["state"] == "EXECUTED":
            return make_result(
                "already_executed",
                tier=Tier(row["tier"]),
                verb=row["verb"],
                grant_id=grant_id,
                scope=self._scope,
            )

        if row["state"] != "APPROVED":
            ledger_append(
                conn,
                now=now,
                kind="refused",
                scope=self._scope,
                actor=actor,
                verb=row["verb"],
                tier=row["tier"],
                grant_id=grant_id,
                record_hash=row["record_hash"],
                detail={"code": "not_approved"},
            )
            return make_result(
                "refused/not_approved",
                tier=Tier(row["tier"]),
                verb=row["verb"],
                grant_id=grant_id,
                scope=self._scope,
            )

        record_dict = json.loads(row["record_json"])
        recomputed_hash = sha256(canonical_json(record_dict)).hexdigest()
        if recomputed_hash != row["record_hash"]:
            ledger_append(
                conn,
                now=now,
                kind="refused",
                scope=self._scope,
                actor=actor,
                verb=row["verb"],
                tier=row["tier"],
                grant_id=grant_id,
                record_hash=row["record_hash"],
                detail={"code": "record_mismatch"},
            )
            return make_result(
                "refused/record_mismatch",
                tier=Tier(row["tier"]),
                verb=row["verb"],
                grant_id=grant_id,
                scope=self._scope,
            )

        record = ActionRecord(**record_dict)

        if record.policy_version != POLICY_VERSION:
            conn.execute(
                "UPDATE grants SET state='REJECTED', reason='stale_policy' WHERE id=?", (grant_id,)
            )
            release_slot_for_grant(conn, grant_id=grant_id, now=now)
            ledger_append(
                conn,
                now=now,
                kind="rejected",
                scope=self._scope,
                actor=actor,
                verb=row["verb"],
                tier=row["tier"],
                grant_id=grant_id,
                record_hash=row["record_hash"],
                detail={"code": "stale_policy"},
            )
            return make_result(
                "refused/stale_policy",
                tier=Tier(row["tier"]),
                verb=row["verb"],
                grant_id=grant_id,
                scope=self._scope,
            )

        definition = self._registry.get(record.tool)
        current_version = definition.current_version(conn, record.resource)
        if current_version != record.resource_version:
            conn.execute(
                "UPDATE grants SET state='REJECTED', reason='stale_resource' WHERE id=?",
                (grant_id,),
            )
            release_slot_for_grant(conn, grant_id=grant_id, now=now)
            ledger_append(
                conn,
                now=now,
                kind="rejected",
                scope=self._scope,
                actor=actor,
                verb=row["verb"],
                tier=row["tier"],
                grant_id=grant_id,
                record_hash=row["record_hash"],
                detail={"code": "stale_resource"},
            )
            return make_result(
                "refused/stale_resource",
                tier=Tier(row["tier"]),
                verb=row["verb"],
                grant_id=grant_id,
                scope=self._scope,
            )

        outcome_code = definition.effect(conn, record, str(grant_id))
        cur = conn.execute(
            "UPDATE grants SET state='EXECUTED', outcome_code=?, executed_at=? "
            "WHERE id=? AND state='APPROVED'",
            (outcome_code, now.isoformat(), grant_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("grant changed state during execution")

        ledger_append(
            conn,
            now=now,
            kind="executed",
            scope=self._scope,
            actor=actor,
            verb=row["verb"],
            tier=row["tier"],
            grant_id=grant_id,
            record_hash=row["record_hash"],
            detail={"code": "executed", "outcome": outcome_code},
        )
        return make_result(
            "executed",
            tier=Tier(row["tier"]),
            verb=row["verb"],
            grant_id=grant_id,
            scope=self._scope,
        )

    def run_direct(
        self,
        *,
        definition: ToolDefinition,
        record: ActionRecord,
        record_hash_value: str,
        tier: Tier,
        verb: str,
        proposer: str,
        now: datetime,
    ) -> Result:
        rail = rail_for(verb)
        try:
            with self._db.transaction() as conn:
                if rail is not None:
                    family, cap = rail
                    if not rail_available(conn, scope=self._scope, family=family, cap=cap, now=now):
                        ledger_append(
                            conn,
                            now=now,
                            kind="refused",
                            scope=self._scope,
                            actor=proposer,
                            verb=verb,
                            tier=tier.value,
                            grant_id=None,
                            record_hash=None,
                            detail={"code": "rail_full"},
                        )
                        return make_result("refused/rail_full", tier=tier, verb=verb)

                idempotency_key = secrets.token_urlsafe(8)
                outcome_code = definition.effect(conn, record, idempotency_key)

                if rail is not None:
                    family, cap = rail
                    reserve_slot(conn, scope=self._scope, family=family, grant_id=None, now=now)

                ledger_append(
                    conn,
                    now=now,
                    kind="executed",
                    scope=self._scope,
                    actor=proposer,
                    verb=verb,
                    tier=tier.value,
                    grant_id=None,
                    record_hash=record_hash_value,
                    detail={"code": "executed", "outcome": outcome_code},
                )
                return make_result("executed", tier=tier, verb=verb, scope=self._scope)
        except Exception:  # noqa: BLE001 - fail closed on any effect or ledger failure (spec 1.17)
            with self._db.transaction() as conn:
                ledger_append(
                    conn,
                    now=now,
                    kind="failed",
                    scope=self._scope,
                    actor=proposer,
                    verb=verb,
                    tier=tier.value,
                    grant_id=None,
                    record_hash=record_hash_value,
                    detail={"code": "execution_error"},
                )
            return make_result("failed/execution_error", tier=tier, verb=verb, scope=self._scope)
