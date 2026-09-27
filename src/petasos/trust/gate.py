"""Gate.propose(tool, arguments, proposer): the one entry point for MCP and browser
callers, wiring hard constraints, the registry, validation, resolution, tiering,
staging, and direct actions (spec 1.2, 1.5, 1.17, 1.19).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from petasos.ledger.chain import append as ledger_append
from petasos.trust.constraints import resource_is_hard_constrained, tool_is_hard_constrained
from petasos.trust.executor import Executor
from petasos.trust.grants import GrantStore
from petasos.trust.outcomes import Result, make_result
from petasos.trust.record import ActionRecord
from petasos.trust.record import record_hash as compute_record_hash
from petasos.trust.risk import POLICY_VERSION, Tier, derive_tier, verb_for
from petasos.trust.tools import ToolRegistry

if TYPE_CHECKING:
    from petasos.storage import Database


class Gate:
    def __init__(
        self,
        db: Database,
        registry: ToolRegistry,
        approvers: frozenset[str],
        scope: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._registry = registry
        self._scope = scope
        self._clock = clock
        self.grants = GrantStore(db, approvers=approvers, scope=scope, clock=clock)
        self.executor = Executor(db, registry=registry, scope=scope, clock=clock)

    def propose(self, tool: str, arguments: Mapping[str, Any], proposer: str) -> Result:
        now = self._clock()

        if tool_is_hard_constrained(tool):
            self._log_refusal("hard_constraint", proposer, now)
            return make_result("refused/hard_constraint")

        definition = self._registry.get(tool)
        if definition is None:
            self._log_refusal("unknown_tool", proposer, now)
            return make_result("refused/unknown_tool")

        conn = self._db.connect()
        try:
            try:
                validated = definition.validate(dict(arguments))
                resolved = definition.resolve(conn, validated)
            except Exception:  # noqa: BLE001 - any validate/resolve failure fails closed (spec 1.19)
                self._log_refusal("invalid_arguments", proposer, now)
                return make_result("refused/invalid_arguments")
        finally:
            conn.close()

        if resource_is_hard_constrained(resolved.resource):
            self._log_refusal("hard_constraint", proposer, now)
            return make_result("refused/hard_constraint")

        tier = derive_tier(definition.profile)
        verb = verb_for(definition.profile)
        record = ActionRecord(
            tool=tool,
            arguments=validated,
            destination=resolved.destination,
            amount_minor=resolved.amount_minor,
            currency=resolved.currency,
            resource=resolved.resource,
            resource_version=resolved.resource_version,
            proposer=proposer,
            policy_version=POLICY_VERSION,
            scope=self._scope,
        )
        record_hash_value = compute_record_hash(record)

        if tier in (Tier.L4, Tier.L5):
            return self.grants.stage(
                record=record,
                record_hash_value=record_hash_value,
                tier=tier,
                verb=verb,
                actor=proposer,
                now=now,
            )

        return self.executor.run_direct(
            definition=definition,
            record=record,
            record_hash_value=record_hash_value,
            tier=tier,
            verb=verb,
            proposer=proposer,
            now=now,
        )

    def _log_refusal(self, code: str, proposer: str, now: datetime) -> None:
        with self._db.transaction() as conn:
            ledger_append(
                conn,
                now=now,
                kind="refused",
                scope=self._scope,
                actor=proposer,
                verb=None,
                tier=None,
                grant_id=None,
                record_hash=None,
                detail={"code": code},
            )
