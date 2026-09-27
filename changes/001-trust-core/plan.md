# Plan: 001-trust-core

grounded_at: `12b04a2` (the `main` commit that merged the 000-bootstrap build, PR #10,
2026-09-27; the planning thread set this value after that merge, and the builder only
checks it)

lane: claude

## Wall

wall_expected:
- `src/petasos/storage.py`
- `src/petasos/trust/**`
- `src/petasos/ledger/**`
- `tests/test_storage_*.py`
- `tests/test_trust_*.py`
- `tests/test_ledger_*.py`
- `changes/001-trust-core/report.md`
- `changes/001-trust-core/tasks.md` (ticking boxes only)

wall_forbidden: everything standing-forbidden in AGENTS.md section 1. Do not touch
`src/petasos/__init__.py` (change 000's), `src/petasos/mcp/**`, `src/petasos/memory/**`,
`src/petasos/helpdesk/**`, or `src/petasos/owner/**`: those belong to other changes.

The merge gate enforces this list mechanically: it reads `wall_expected` from this file on
`main` and refuses to merge a pull request that changes any other path.

## Parallel-safety note

Change 002 (memory-canary-guard) may build after this one merges. It will use
`petasos.storage.Database` from this change, so its spec is written against the merged
interface, not in parallel.

## Build order

Build the parts each later part depends on first. Module names follow ARCHITECTURE.md
section 3.

1. `storage.py`: `Database` (spec 1.9). Tests: no file on construction, WAL and busy timeout
   set, `transaction()` rolls back on exception (acceptance test 38).
2. `ledger/store.py` and `ledger/chain.py`: schema, `append(conn, ...)` that writes inside the
   caller's transaction, `verify()` (spec 1.22, 1.23; acceptance tests 34, 35).
3. `trust/risk.py`: `RiskProfile`, `InvalidProfile`, `Tier`, `derive_tier`, `verb_for`,
   `rail_for`, `POLICY_VERSION` (spec 1.1, 1.3, 1.4, 1.8, 1.13; acceptance tests 1 to 4).
   Test 2 is the hypothesis test (ARCHITECTURE.md section 8, D-003).
4. `trust/record.py`: `ActionRecord`, `canonical_json`, `record_hash` (spec 1.6, 1.7;
   acceptance test 6).
5. `trust/tools.py`: `ToolDefinition`, `ToolRegistry`, `Resolved`, `InvalidArguments`,
   `NotFound` (spec 1.2).
6. `trust/outcomes.py`: the result-code sentence table (spec 1.25; acceptance test 37).
7. `trust/constraints.py`: hard constraints (spec 1.18; acceptance tests 30, 31).
8. `trust/grants.py`: `GrantStore` with stage, `list_pending`, approve, abort, `abort_all`,
   sweep, rails, and scope on every query (spec 1.10 to 1.15, 1.21, 1.26; acceptance tests
   7 to 18, 28, 39, 40).
9. `trust/executor.py`: `Executor.run` (spec 1.16; acceptance tests 19 to 27).
10. `trust/gate.py`: `Gate.propose` wiring constraints, registry, validation, resolution,
    tiering, staging, and direct actions (spec 1.2, 1.5, 1.17, 1.19; acceptance tests 5, 29,
    32, 33), then the ledger-content scan (acceptance test 36).

Tests define their own fake refund, email, and note tools, and create their own fake effect
tables in the temporary database. Every test database lives under pytest's `tmp_path`.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if a race test (8, 10, 18, 21, 27) is flaky on the CI
runner rather than deterministically passing, do not add sleeps or retries to make it pass;
flag it with the failure output.
