# Proposal: 001-trust-core

status: proposed
lane: claude
depends_on: 000-bootstrap
decisions: D-001, D-003, D-009, D-013

## Why

Everything else in Petasos sits on top of this: the rules that decide how dangerous an action is, and the grants that make a human approve the dangerous ones. If this module is right, the MCP server and the demo app are thin. If it is wrong, nothing else matters.

## What this change delivers

The `petasos.trust` and `petasos.ledger` packages, fully tested, with no server and no UI:

1. A `Manifest` type describing an action (no tier field).
2. `derive_tier(manifest)`: pure, rule-based, monotone (adding a risk flag can only raise the tier).
3. `verb_for(manifest)`: the human-facing verb, severity-ordered, with a test proving every verb that can be produced has an executor registered.
4. `GrantStore`: stage, list (with expiry sweep), approve by compare-and-swap, abort, per-verb-family hourly rails, and the burn-on-verb-mismatch rule.
5. `Executor`: re-verifies the manifest hash and runs the registered tool.
6. Hard constraints that deny regardless of tier.
7. A hash-chained ledger with `verify()` that names the first broken row, and a genesis row that cannot be silently rewritten.
8. A plain-language outcome for every gate result (a small fixed table, not a model).

## What this change does not deliver

No HTTP, no MCP, no memory or canaries (002), no demo tools (003), no UI (004). No LLM anywhere.

## Reader test

After this merges, a reader can open `src/petasos/trust/tiers.py`, read the rule table top to bottom, and explain to a colleague why "reply to a customer" is L4 and "issue a refund" is L5 without running anything.
