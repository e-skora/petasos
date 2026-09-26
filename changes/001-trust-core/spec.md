# Spec: 001-trust-core

Binding once the proposal is ratified. The builder implements this table; a task that seems to need a different answer is a stop-and-flag.

## 1. Decisions for this change

| # | Decision | Choice | Why |
|---|---|---|---|
| 1.1 | Manifest fields | `tool: str`, `mutation: Literal["none","internal","external"]`, `reversible: bool`, `touches_money: bool`, `touches_secrets: bool`, `deletes: bool`, `posts_public: bool`, `target: str` (free text, for the ledger only), `proposer: str` (client id) | The smallest set that lets the rule table be read in one screen. Extra fields are a new decision. |
| 1.2 | Tier rule (max over floors) | `L1` if mutation none; `L2` if internal and reversible; `L3` if internal and not reversible; `L4` if external; `L5` if any of touches_money, touches_secrets, deletes, posts_public | Monotone by construction: each flag only adds a floor. |
| 1.3 | Direct-execute cutoff | L1 to L3 execute without a grant; L4 and L5 always stage | The demo's read/write contrast. |
| 1.4 | Verb table (severity order, most severe first) | `ISSUE-REFUND` (money), `DELETE-TICKET` (deletes), `PUBLISH` (posts_public), `REVEAL-SECRET` (secrets), `SEND-EMAIL` (external), `CHANGE-RECORD` (internal, not reversible), `ADD-NOTE` (internal, reversible), `READ` (none) | The human reads the verb; the most severe applicable property names it. |
| 1.5 | Token | `secrets.token_urlsafe(16)`, single use | Enough entropy for a public demo; the compare-and-swap makes reuse impossible anyway. |
| 1.6 | Grant states | `AWAITING`, `APPROVED`, `EXECUTED`, `REJECTED` (with `reason`), `EXPIRED`, `ABORTED` | EXPIRED is its own state, never folded into REJECTED. |
| 1.7 | TTL | 24 hours from staging; swept on every read (`list`, `approve`, `abort`) | A lapsed grant is never reported as waiting. |
| 1.8 | Approve semantics | `approve(token, verb, approver)` runs inside ONE `BEGIN IMMEDIATE` transaction (SQLite serializes writers, so two concurrent approvals cannot interleave). Step 0: if `approver` is not in the store's `approvers` set (see 1.17), return `refused/not_an_approver` before any write; the grant is untouched. Step 1: `UPDATE grants SET state='APPROVED', approved_by=?, approved_at=? WHERE state='AWAITING' AND verb=? AND token=? AND expires_at>?`. If one row changed: success. Step 2 (only if zero rows changed): `UPDATE grants SET state='REJECTED', reason='verb_mismatch' WHERE state='AWAITING' AND token=? AND expires_at>?`. If one row changed: the token was valid but the verb was wrong, so the grant is burned; return `refused/verb_mismatch`. If zero rows changed: return `refused/unknown_or_used_or_expired` (one code; the caller is not told which). A partial unique index on `(verb, token) WHERE state='AWAITING'` guarantees a token is live for at most one grant. | The asymmetry (wrong verb burns, wrong caller does not) is the demo; the transaction makes the two-statement burn safe under concurrency. |
| 1.9 | Rate rails | per verb family per trailing hour: `money` (ISSUE-REFUND) 2, `destructive` (DELETE-TICKET, PUBLISH, REVEAL-SECRET) 2, `external` (SEND-EMAIL) 5, `internal` (CHANGE-RECORD, ADD-NOTE) 30; ONE predicate `rail_for(verb)` used by both `stage` and `approve` | Verb-keyed so one busy family cannot starve another; one predicate so the two paths cannot disagree. |
| 1.10 | Abort | `abort(token)` moves AWAITING to ABORTED; `abort_all()` for the owner; both ledgered | Kill switch. |
| 1.11 | Executor | `run(grant, registry)`: refuse unless state APPROVED; recompute `manifest_hash`; refuse on mismatch; call the registered callable; move to EXECUTED; ledger both refusals and executions | Closes the window between approval and execution. |
| 1.12 | Hard constraints | deny if `tool` is in `{"disable_gate","rotate_canaries","edit_ledger"}` or `target` matches a forbidden-path pattern; checked before tier derivation; no unlock path from request content | Above the ladder. |
| 1.13 | Ledger row | `id, ts, kind, actor, verb, tier, grant_id, detail_json, prev_hash, hash` with `hash = sha256(canonical_json(row_without_hash))`; genesis row hash computed from its own fields; `verify()` walks in id order and returns the first row whose hash or prev_hash does not match, or `None` | Names the break, does not just say "broken". |
| 1.14 | Which tables are chained | `ledger` only. `grants` and `rate_events` are not chained, and `ledger/store.py` says so in a docstring and the README's honesty note | Talaria's honest coverage statement, kept. |
| 1.15 | Clock | every store and the executor take a `clock: Callable[[], datetime]`; default `datetime.now(UTC)`; tests inject a frozen clock | No frozen-date constants in tests. |
| 1.16 | Outcome sentences | a fixed dict from result code to plain sentence, e.g. `verb_mismatch` -> "That approval was for a different action, so it was thrown away." | Never a code on a screen. |
| 1.17 | Who may approve | `GrantStore(approvers=frozenset({...}), clock=...)`. The trust core is identity-agnostic: it does not know what a client is; it only checks that the `approver` string passed to `approve` and `abort` is in the set it was constructed with. Change 003 builds that set from the client registry (identities carrying the `approver` flag). | Keeps 001 free of any HTTP or identity code. |
| 1.18 | Manifest hash | `sha256` of the canonical JSON of ALL manifest fields including `target` and `proposer` (sorted keys, no whitespace, UTF-8). Stored on the grant at staging as `manifest_hash`, alongside the manifest itself as `manifest_json`. The executor recomputes the hash from the manifest it is about to run and compares. | Every field is part of what the human approved. |
| 1.19 | Grant views | `Grant.for_proposer()` returns a copy with `token=None`; `Grant.for_approver()` returns the full record. `GrantStore.stage` returns the full `Grant`; callers that answer a proposer must send `for_proposer()`. `GrantStore.list_pending(viewer_is_approver: bool)` applies the same rule. A test asserts `for_proposer().token is None`. | The token never reaches the client that proposed the action. |

## 2. Public interface

```python
from petasos.trust import Manifest, Tier, derive_tier, verb_for, GrantStore, Executor, HardConstraintDenied
from petasos.ledger import Ledger

m = Manifest(tool="reply_to_customer", mutation="external", reversible=False, touches_money=False,
             touches_secrets=False, deletes=False, posts_public=False, target="ticket 42", proposer="agent")
derive_tier(m)            # Tier.L4
verb_for(m)               # "SEND-EMAIL"
store = GrantStore(path=tmp_sqlite, approvers=frozenset({"owner"}), clock=clock)
g = store.stage(m)        # Grant(state="AWAITING", token=..., verb="SEND-EMAIL", expires_at=...)
g.for_proposer().token    # None
store.approve(token=g.token, verb="SEND-EMAIL", approver="owner")   # ApproveResult(ok=True, ...)
executor.run(g.id)        # ExecuteResult(ok=True, outcome="Email recorded as sent to the customer.")
ledger.verify()           # None
```

## 3. Acceptance tests (plain language; each becomes a test named for it)

1. The Manifest dataclass has no attribute containing "tier" (checked by reflection).
2. For every manifest in a generated set (hypothesis), setting any one risk flag from False to True never lowers the tier.
3. Every verb `verb_for` can return has an executor registered in the test registry; the test enumerates the verb table.
4. Staging an L4 manifest returns a grant whose token is not present in the value returned to the proposer path (the store returns two views: `for_proposer()` without the token, `for_approver()` with it).
5. Two threads approving the same token concurrently: exactly one succeeds.
6. Right token, wrong verb: the grant is REJECTED with reason `verb_mismatch`, and a second approve with the right verb is refused.
7. Right verb, caller not an approver: refused; the grant remains AWAITING and the same token then succeeds for an approver.
8. A grant older than 24 hours (frozen clock advanced) is reported EXPIRED by `list`, and `approve` refuses it.
9. Six SEND-EMAIL stagings in one hour: the sixth is refused with a rail message; an ADD-NOTE staging in the same hour succeeds.
10. Changing one manifest field between staging and execution makes the executor refuse, with a ledger row.
11. A manifest with `tool="disable_gate"` is denied before tier derivation, at every tier.
12. Editing one ledger row's `detail_json` in SQLite makes `verify()` return that row's id; an untouched ledger returns None.
13. Deleting the genesis row when chained rows exist makes `verify()` report the break rather than recreating genesis.
14. Every result code in the outcome table has a sentence, and no sentence contains a code or an id.
15. The whole module imports with no network, no environment variable, and no file outside a temporary directory.
16. Two threads, one approving with the right verb and one with the wrong verb, on the same token at the same time: exactly one outcome is recorded, either APPROVED or REJECTED/verb_mismatch, never both and never neither; the ledger holds exactly one row for the token's resolution.
17. `list_pending(viewer_is_approver=False)` never includes a token; with `True` it does.
