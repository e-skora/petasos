# Spec: 001-trust-core

Binding once the proposal is ratified. The builder implements this table; a task that seems to need a different answer is a stop-and-flag.

Version 2.1, 2026-09-26 (2.1 answers the second-pass review: scope 1.26, rail wording 1.13, policy-version reference 1.8).

Version 2, 2026-09-26. Rewritten after the first plan review (`review/2026-09-26-plan-review.md`, findings 1, 2, 11, 13) and Elias's rulings D-016 and D-017. The central change: a grant now authorizes one **immutable action record** (the exact validated arguments), not a description of risk, and execution happens in **one database transaction**.

## 0. Terms used here

- **Tool definition**: server-owned code describing one action the system can take: its name, its risk profile, how to validate its arguments, how to look up what it acts on, and its effect. A caller names a tool and passes arguments; it never supplies risk facts.
- **Risk profile**: the risk facts on a tool definition (what it mutates, whether it is reversible, whether it moves money or deletes). It has no tier field; the tier is derived from it.
- **Action record**: the exact action a person approves: tool, validated arguments, destination, amount and currency, the resource and its version, the proposer, and the policy version. Frozen at proposal time.
- **Record hash**: SHA-256 of the action record's canonical JSON. Stored beside the record so tampering is detectable.
- **Resource version**: an integer the domain bumps whenever the thing acted on (a ticket) changes. An approval of version 3 cannot execute against version 4.
- **Policy version**: a constant naming the current tier, verb, and rail tables. Changing any of them bumps it.
- **Effect**: the tool definition's function that changes data. It receives the same database connection as the grant update and the ledger append, so all three commit or none do.
- **Rail**: an hourly cap on how many actions of one family may be staged or run.

## 1. Decisions for this change

| # | Decision | Choice | Why |
|---|---|---|---|
| 1.1 | Risk profile fields | `RiskProfile(mutation: Literal["none","internal","external"], reversible: bool, touches_money: bool, deletes: bool)`. Frozen. `mutation="none"` with any other flag set, or with `reversible=False`, raises `InvalidProfile` at construction. `touches_secrets` and `posts_public` are gone: no v1 tool can produce them. | The smallest set the v1 tools need; every field changes a tier or a verb. |
| 1.2 | Tool definitions are server-owned | `ToolDefinition(name, profile, validate, resolve, effect, current_version)`. `ToolRegistry(definitions)` rejects duplicate names at construction. `Gate.propose(tool, arguments, proposer)` has no parameter that carries risk, tier, verb, or approver. | Review finding 1: a caller must never supply its own authoritative risk flags. |
| 1.3 | Tier rule (max over floors) | `L1` if mutation none; `L2` if internal and reversible; `L3` if internal and not reversible; `L4` if external; `L5` if `touches_money` or `deletes`. | Monotone by construction: each flag only adds a floor. |
| 1.4 | Expected tier and verb table | Every valid profile has exactly one expected (tier, verb). Verb, most severe first: `ISSUE-REFUND` (touches_money), `DELETE-TICKET` (deletes), `SEND-EMAIL` (external), `CHANGE-RECORD` (internal, not reversible), `ADD-NOTE` (internal, reversible), `READ` (none). The test enumerates all 17 valid profiles and asserts both values for each. | A classifier that returned L5 for everything would pass a monotonicity test alone (review finding 11). |
| 1.5 | Direct-execute cutoff | L1 to L3 execute at once through the executor (one transaction, ledgered, rails apply). L4 and L5 always stage a grant. | The demo's read/write contrast. |
| 1.6 | Action record | `ActionRecord(tool: str, arguments: Mapping[str, JSON], destination: str \| None, amount_minor: int \| None, currency: str \| None, resource: str, resource_version: int, proposer: str, policy_version: str, scope: str)`. Frozen. `arguments` is what `validate` returned, never the raw input. `destination`, `amount_minor`, `currency`, `resource`, and `resource_version` come from `resolve`, which reads current data through the connection. `amount_minor` is an integer in the currency's minor unit (4200 means 42.00); `currency` is an upper-case ISO 4217 code; both are set together or both are `None`. | Review finding 1: a $42 refund and a $4,200 refund must be different records. |
| 1.7 | Canonical JSON and record hash | `canonical_json(obj)`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` encoded UTF-8. Floats are refused anywhere in a record (`TypeError`); amounts are integers. `record_hash = sha256(canonical_json(asdict(record))).hexdigest()`. | One byte-exact form, so the same record always hashes the same. |
| 1.8 | Policy version | `POLICY_VERSION = "1"` in `trust/risk.py`. Any change to 1.3, 1.4, 1.13, or 1.18 bumps it. The executor refuses a record whose `policy_version` differs from the current one (`refused/stale_policy`). | An approval made under one rulebook never executes under another. |
| 1.9 | One database file | `petasos.storage.Database(path)`: every connection opens WAL mode, `foreign_keys=ON`, `busy_timeout=5000`, `isolation_level=None`. `Database.transaction()` is a context manager running `BEGIN IMMEDIATE` ... `COMMIT`, or `ROLLBACK` on any exception. Each public operation opens its own connection, so threads never share one. `Database.migrate()` creates the tables explicitly; the constructor creates no file and no table. | D-016: the fake effect, the grant transition, and the ledger row must commit together. |
| 1.10 | Token | `secrets.token_urlsafe(16)`. A unique index on `token` alone, across every state, so a token can never name two grants. Single consumption comes from the conditional update in 1.12, not from the index. | Review finding 11: the old `(verb, token)` index allowed one token under two verbs. |
| 1.11 | Grant states | `AWAITING`, `APPROVED`, `EXECUTED`, `REJECTED` (with `reason`), `EXPIRED`, `ABORTED`. `EXPIRED` is its own state, never folded into `REJECTED`. | A lapsed grant is never reported as waiting or as refused. |
| 1.12 | Approve semantics | `approve(token, verb, approver)`. Step 0, before any write: if `approver` is not in the store's `approvers` set, return `refused/not_an_approver`; the grant is untouched. Then ONE transaction: sweep expiry (1.14); `UPDATE grants SET state='APPROVED', approved_by=?, approved_at=? WHERE token=? AND verb=? AND state='AWAITING' AND expires_at>? AND proposer<>?`. One row changed: `approved`. Zero rows: `UPDATE grants SET state='REJECTED', reason='verb_mismatch' WHERE token=? AND verb<>? AND state='AWAITING' AND expires_at>?`; one row changed: `refused/verb_mismatch` (the grant is burned). Otherwise, if the token names an AWAITING grant whose proposer is the approver: `refused/self_approval` (grant untouched). Otherwise `refused/unknown_or_used_or_expired` (one code; the caller is not told which). Each outcome appends its ledger row in the same transaction. | Wrong verb burns, wrong caller does not; nobody approves their own proposal. |
| 1.13 | Rails | Families and hourly caps: `money` (ISSUE-REFUND) 2, `destructive` (DELETE-TICKET) 2, `external` (SEND-EMAIL) 5, `internal` (CHANGE-RECORD, ADD-NOTE) 30. `READ` has no rail. One function `rail_for(verb) -> (family, cap) \| None`. A slot is reserved in the same transaction that stages a grant or runs a direct action: count slots of that family reserved in the trailing hour and not released; at the cap, refuse with `refused/rail_full` and write nothing but the ledger row. Approval never charges again. Rejection, abort, and expiry release the slot. Execution keeps it. The rail is an **admission limit**: it caps how many actions of a family may be staged or run directly in any trailing hour, counted per scope (1.26). It does not separately cap executions per hour: grants live 24 hours, so approvals of grants staged in different hours may execute in the same hour, and each of those still needed its own human approval. | Review finding 11: the fifth staged email must still be approvable. Second-pass finding 10: say plainly what the cap limits. |
| 1.14 | TTL and sweep | 24 hours from staging. Every public operation first moves `AWAITING` and `APPROVED` grants with `expires_at <= now` to `EXPIRED`, releasing their slots and appending one `expired` ledger row each, inside its own transaction. A grant is live only while `now < expires_at`. | The boundary instant counts as expired, never as waiting. |
| 1.15 | Abort | `abort(token, approver)` moves an `AWAITING` or `APPROVED` grant to `ABORTED` and releases its slot; `abort_all(approver)` does the same for every live grant in the Gate's scope (1.26). Both refuse a non-approver with `refused/not_an_approver`. Both are one transaction with their ledger rows. | Kill switch; the review noted the old interface had no caller. |
| 1.16 | Executor | `run(grant_id)`, ONE transaction: sweep; read the grant. `EXECUTED`: return `already_executed` with the stored outcome code, write nothing. Not `APPROVED`: `refused/not_approved`. Recompute the record hash from the stored `record_json`; mismatch: `refused/record_mismatch`. `policy_version` differs: `refused/stale_policy` (grant moves to `REJECTED`, reason `stale_policy`, slot released). `current_version(conn, resource)` differs from the record's `resource_version`: `refused/stale_resource` (same move, reason `stale_resource`). Otherwise call `effect(conn, record, idempotency_key=grant_id)`, then `UPDATE grants SET state='EXECUTED', outcome_code=?, executed_at=? WHERE id=? AND state='APPROVED'` (must change exactly one row, else roll back), then append the `executed` ledger row, then commit. Any exception anywhere rolls the whole transaction back: no effect, grant still `APPROVED`, and a separate transaction appends a `failed` ledger row; if even that fails, the error propagates. Every refusal appends a `refused` ledger row in the same transaction. The executor never accepts a record from its caller. | Review finding 2: one approval, at most one effect, and a retry returns the first outcome. |
| 1.17 | Direct actions | `Gate.propose` for an L1 to L3 action builds the record and runs the same executor path without a grant (idempotency key: a fresh id), in one transaction with its rail slot and ledger row. | One code path for every effect. |
| 1.18 | Hard constraints | Checked first in `Gate.propose`, before the registry lookup: deny `refused/hard_constraint` if the tool name is in `{"disable_gate", "rotate_canaries", "edit_ledger"}`, or, after `resolve`, if `resource` matches any of `ledger:*`, `grant:*`, `canary:*`, `config:*`. Nothing in the request can unlock them. | Above the ladder; the review asked for the patterns to be listed. |
| 1.19 | Proposal refusals | Unknown tool: `refused/unknown_tool`. `validate` raises `InvalidArguments` or `resolve` raises `NotFound`: `refused/invalid_arguments`. Any other exception from `validate` or `resolve`: `refused/invalid_arguments` too (fail closed). Each appends a `refused` ledger row. | Every malformed request resolves restrictively. |
| 1.20 | Who may approve | `approvers: frozenset[str]` given to the `Gate` at construction. The trust core is identity-agnostic: 003 and 004 pass the authenticated identity's id as `approver`, never a value from request content. | Keeps 001 free of HTTP and identity code. |
| 1.21 | Grant views | `Grant.for_proposer()` returns a copy with `token=None`. `Grant.for_approver()` returns the token and the full action record. `propose` returns a `Result` with no token. `list_pending(viewer)` returns approver views only when `viewer` is in `approvers`, proposer views otherwise. | The token never reaches the proposer; the approval card and the executor read the same record. |
| 1.22 | Ledger row | `id, ts, kind, scope, actor, verb, tier, grant_id, record_hash, detail_json, prev_hash, hash`; `hash = sha256(canonical_json(row without hash))`; the genesis row's `prev_hash` is 64 zeros. `kind` is one of `staged`, `executed`, `approved`, `rejected`, `refused`, `aborted`, `expired`, `failed`. `actor` is an identity id. `detail_json` holds result codes and counts only. **No ledger column ever holds caller-supplied text** (no arguments, no destination, no unknown tool name); the record hash stands in for the content. `verify()` walks in id order and returns the id of the first row whose hash or `prev_hash` does not match, or `None`. | Review finding 13: the audit trail outlives the hourly reset, so it must hold no visitor text. |
| 1.23 | What the ledger claims | `ledger/store.py`'s docstring says: only the `ledger` table is chained; `verify()` shows the chain is internally consistent; someone who can rewrite the file can recompute every later hash, so this is tamper-evidence against edits, not proof against a rewrite. | The review's probe showed a full rewrite passes `verify()`. |
| 1.24 | Clock | Everything that reads time takes `clock: Callable[[], datetime]` (UTC). Tests inject a frozen clock and derive every date from it. | No frozen-date constants in tests. |
| 1.25 | Outcome sentences | `outcomes.SENTENCES`: a fixed dict from every result code in this spec to one plain sentence, e.g. `refused/verb_mismatch` -> "That approval was for a different action, so it was thrown away." Every `Result` carries `code` and `sentence`. | Never a code on a screen. |
| 1.26 | Scope | `Gate(db, registry, approvers, scope, clock)`. `scope` is a trusted string from the calling layer (change 003 passes the visitor session id; tests pass any id), never from request content. It is stored on every grant and rail slot, is a field of the action record (so it is hashed), and is a ledger column. Every read and write in `GrantStore` and `Executor` filters on it: token lookup, `list_pending`, `approve`, `abort`, `abort_all`, the expiry sweep, rail counting, and `run(grant_id)`. A grant from another scope behaves exactly like an unknown one (`refused/unknown_or_used_or_expired` for tokens, `refused/not_approved` for `run`). Rails count per scope; the global abuse ceilings across all scopes (D-017) are change 003's, in their own table. | D-017: one visitor's owner can never see, approve, abort, or run another visitor's work (second-pass finding 9). |

Result codes (the complete list): `staged`, `approved`, `executed`, `already_executed`, `aborted`, `refused/unknown_tool`, `refused/hard_constraint`, `refused/invalid_arguments`, `refused/rail_full`, `refused/not_an_approver`, `refused/self_approval`, `refused/verb_mismatch`, `refused/unknown_or_used_or_expired`, `refused/not_approved`, `refused/record_mismatch`, `refused/stale_policy`, `refused/stale_resource`, `failed/execution_error`.

## 2. Public interface

```python
from petasos.storage import Database
from petasos.trust import Gate, ToolDefinition, ToolRegistry, RiskProfile, Tier
from petasos.ledger import Ledger

db = Database(tmp_path / "petasos.sqlite")
db.migrate()
refund = ToolDefinition(
    name="issue_refund",
    profile=RiskProfile(mutation="external", reversible=False, touches_money=True, deletes=False),
    validate=validate_refund,  # raw dict -> validated dict, or raises InvalidArguments
    resolve=resolve_refund,  # (conn, validated) -> Resolved(destination, amount, resource, version)
    effect=record_refund,  # (conn, record, idempotency_key) -> outcome code
    current_version=ticket_version,  # (conn, resource) -> int
)
gate = Gate(
    db, ToolRegistry([refund]), approvers=frozenset({"owner"}), scope="session-1", clock=clock
)

r = gate.propose(
    "issue_refund", {"ticket": 42, "amount": "42.00", "currency": "USD"}, proposer="agent"
)
# Result(code="staged", tier=Tier.L5, verb="ISSUE-REFUND", grant_id=...), no token
card = gate.grants.list_pending(viewer="owner")[0]  # token plus the exact action record
gate.grants.approve(token=card.token, verb="ISSUE-REFUND", approver="owner")  # approved
gate.executor.run(r.grant_id)  # executed: refund row, grant EXECUTED, ledger row, one commit
gate.executor.run(r.grant_id)  # already_executed: nothing new written
Ledger(db).verify()  # None
```

The tool functions in this example live in the tests for 001; the real help-desk tools arrive in 003.

## 3. Acceptance tests (plain language; each becomes a test named for it)

Risk and records

1. `RiskProfile` has no attribute containing "tier" (reflection).
2. For every valid profile (hypothesis), setting any one of `touches_money` or `deletes` to True, making `reversible` False, or raising `mutation` never lowers the tier.
3. All 17 valid profiles map to exactly the expected (tier, verb) pair in 1.3 and 1.4; each invalid profile raises `InvalidProfile`.
4. Every verb in the table except `READ` has a rail; `rail_for("READ")` is `None`.
5. `Gate.propose`'s signature has only `tool`, `arguments`, and `proposer`; no parameter name contains risk, tier, verb, flag, or approver. `Executor.run`'s signature has only `grant_id`.
6. Two records that differ in exactly one field hash differently, for every field of `ActionRecord`; a record containing a float is refused.

Staging, approval, expiry

7. Proposing an L5 refund returns `staged` with no token; `list_pending(viewer="agent")` shows no token; `list_pending(viewer="owner")` shows the token and the full record.
8. Two threads approving the same token at once: exactly one `approved`; the other `refused/unknown_or_used_or_expired`.
9. Right token, wrong verb: grant `REJECTED` (`verb_mismatch`); a later approve with the right verb is refused.
10. Right token and verb, one thread, wrong verb in another, at once: exactly one of `APPROVED` or `REJECTED/verb_mismatch` results, and exactly one resolution ledger row exists.
11. A caller not in `approvers`: `refused/not_an_approver`; the grant stays `AWAITING` and then approves for `owner`.
12. An approver approving a grant they proposed: `refused/self_approval`; the grant stays `AWAITING`.
13. With the frozen clock at exactly `expires_at`, the grant is `EXPIRED` in `list_pending` and `approve` refuses it; one second earlier it approves.
14. An `APPROVED` grant past its expiry is `EXPIRED` and the executor refuses it.

Rails

15. Five SEND-EMAIL stagings in one hour succeed and the sixth is `refused/rail_full`; an ADD-NOTE in the same hour succeeds.
16. All five staged emails can be approved and executed (approval does not charge the rail).
17. Aborting one of the five frees a slot: a sixth staging then succeeds. The same holds for a verb-mismatch burn and for a `stale_resource` rejection.
18. Two threads staging the last free slot at once: exactly one succeeds.

Execution

19. Approve then run: the fake effect row, the grant's `EXECUTED` state, and the `executed` ledger row all exist, and they were written by one commit (the test's effect asserts it is inside a transaction on the connection it was given).
20. Running an executed grant again returns `already_executed` with the first outcome code, and the effect ran exactly once.
21. Two threads running the same approved grant at once: the effect runs exactly once; one gets `executed`, the other `already_executed`.
22. The effect raises after writing its row: no effect row remains, the grant is still `APPROVED`, a `failed` ledger row exists, and a retry then executes once.
23. The ledger append raises inside the execution transaction: no effect row, grant still `APPROVED`.
24. For each field of the action record, editing that field inside the stored `record_json` in SQLite makes `run` return `refused/record_mismatch` with no effect.
25. Bumping the resource's version after approval makes `run` return `refused/stale_resource`; the grant is `REJECTED` and its slot is released.
26. A record whose `policy_version` is not the current one is refused with `refused/stale_policy`.
27. Abort and run racing on the same approved grant: exactly one of `ABORTED` or `EXECUTED`, never both, and the effect ran at most once.
28. `abort_all("owner")` aborts every live grant in its scope and releases those slots; `abort_all("agent")` changes nothing.
29. An L2 direct action executes at once through the same path: effect row, ledger row, and rail slot in one commit.

Proposal refusals and hard constraints

30. `tool="disable_gate"` is `refused/hard_constraint` even though no such tool is registered, and with any arguments.
31. A tool whose `resolve` returns a resource `ledger:1` is `refused/hard_constraint`.
32. An unknown tool is `refused/unknown_tool`, and the ledger row does not contain the tool name.
33. `validate` raising `InvalidArguments`, `resolve` raising `NotFound`, and `resolve` raising `RuntimeError` each give `refused/invalid_arguments`.

Ledger

34. Editing one ledger row's `detail_json` in SQLite makes `verify()` return that row's id; an untouched ledger returns `None`.
35. Deleting the genesis row when chained rows exist makes `verify()` report the break rather than recreating a genesis row.
36. No ledger row written during the whole suite contains any argument value, destination, or unknown tool name used in the tests (the test scans every row for those strings).

Plumbing

37. Every result code in section 1 has a sentence, and no sentence contains a code, an id, or an underscore.
38. Constructing `Database(path)` creates no file; `migrate()` creates it; importing `petasos.trust`, `petasos.ledger`, and `petasos.storage` touches no network, no environment variable, and no file outside a temporary directory.

Added in version 2.1

39. Two Gates on one database with scopes `s1` and `s2`, each with approver `owner`: the `s2` owner cannot list, approve, abort, or run an `s1` grant (each behaves as unknown); `abort_all` in `s2` leaves `s1` grants untouched; five `s1` email stagings do not use `s2`'s rail. Every ledger row carries its scope.
40. With the frozen clock advanced 61 minutes past the sixth refused email, a new email stages even though the first five grants are still live (the rail is an admission limit, 1.13).
