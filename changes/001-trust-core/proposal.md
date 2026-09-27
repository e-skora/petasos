# Proposal: 001-trust-core

status: proposed
lane: claude
depends_on: 000-bootstrap
decisions: D-003, D-009, D-013, D-016

## Why

Everything else in Petasos sits on top of this: the rules that decide how dangerous an action is, and the grants that make a person approve exactly the action that will run. The v1 promise (D-016) is one journey: an AI proposes a $42 refund, a person approves that refund and nothing else, the system runs only what was approved, once, and records it. If this module keeps that promise, the MCP server and the demo app are thin. If it does not, nothing else matters.

## What this change delivers

The `petasos.storage`, `petasos.trust`, and `petasos.ledger` packages, fully tested, with no server and no UI:

1. One SQLite file with explicit migrations and one-transaction helpers, so an effect, a grant change, and its audit row commit together or not at all.
2. Server-owned tool definitions: the caller names a tool and passes arguments; risk comes from the definition, never from the caller.
3. `derive_tier` and the verb table: pure, rule-based, monotone, and checked row by row against an expected table.
4. The immutable action record and its hash: the exact arguments, destination, amount and currency, resource version, proposer, and policy version a person approves.
5. `GrantStore`: stage, list (with an expiry sweep), approve by compare-and-swap, abort, burn on verb mismatch, refuse self-approval, and hourly rails that reserve at staging and release on abort or rejection.
6. `Executor`: runs an approved record in one transaction, refuses a changed record, a stale resource, or a stale policy, and returns the first outcome on a retry without repeating the effect.
7. Hard constraints that deny regardless of tier.
8. A hash-chained ledger that holds no caller-supplied text, with `verify()` naming the first broken row and an honest statement of what it proves.
9. A plain-language sentence for every result code.

## What this change does not deliver

No HTTP, no MCP, no memory or canaries (002), no real help-desk tools (003), no UI (004). No LLM anywhere.

## Reader test

After this merges, a reader can open `src/petasos/trust/risk.py`, read the rule table top to bottom, and explain why "reply to a customer" is L4 and "issue a refund" is L5; then open the executor test and see that changing the refund amount after approval makes the run refuse.
