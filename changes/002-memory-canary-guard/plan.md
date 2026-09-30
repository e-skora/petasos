# Plan: 002-memory-canary-guard

grounded_at: (set by the planning thread when this change is ratified, to the `main` commit
of that moment; the builder checks it and never fills it in)

lane: claude

## Wall

wall_expected:
- `src/petasos/memory/**`
- `src/petasos/storage.py` (only to import `MEMORY_SCHEMA` inside `migrate()`, beside the ledger and grants schemas)
- `tests/test_memory_*.py`
- `tests/test_storage_*.py` (only where a test enumerates the tables `migrate()` creates)
- `changes/002-memory-canary-guard/report.md`
- `changes/002-memory-canary-guard/tasks.md` (ticking boxes only)

wall_forbidden: everything standing-forbidden in AGENTS.md section 1. Do not touch
`src/petasos/__init__.py`, `src/petasos/app.py`, `src/petasos/mcp/**` (000's and 003's),
`src/petasos/trust/**`, `src/petasos/ledger/**` (001's), `src/petasos/helpdesk/**`,
`src/petasos/sessions/**`, or `src/petasos/owner/**` (later changes). The trust core's
should-fix items from the 001 review are not this change's to fix.

The merge gate enforces this list mechanically from the copy of this file on `main`.

## Parallel-safety note

Change 003 (mcp-server-helpdesk) depends on this change: its guard seam calls
`assert_no_private_payload` and `all_canaries`, and its help-desk seed data plants the canary
ticket through `MemoryStore.put_in` inside its session-mint transaction. 003's spec is written against the interface in this spec
(section 2); 003 is grounded only after this change merges. Nothing else builds alongside.

## Build order

1. `memory/store.py`: `MemoryTier`, `Entry`, `InvalidEntry`, `TierLocked`, `MEMORY_SCHEMA`,
   `MemoryStore` with `put`, `put_in`, `read`, `search`, `sweep`, `_sweep_in` (spec 2.1 to
   2.5, 2.7 to 2.10, 2.15). Then the two-line `storage.py` edit and the table-list test
   update (spec 2.2). Acceptance tests 1, 3, 8, 13 to 16, and the `TierLocked` and expiry
   halves of 7 and 9; tests 4 to 6, 9, 10 complete with step 2.
2. `memory/canary.py`: `mint_canary`, `CanarySet`, `canary_set`, `all_canaries`,
   `canary_entry`, and the minting, marker stripping, and re-planting inside `put_in`
   (spec 2.5, 2.6, 2.14). Acceptance tests 4 to 7, 9 to 12.
3. `memory/guard.py`: `Hit`, `GuardTripped`, `scan`, `assert_no_private_payload` with the
   node, byte, and depth bounds and the prefix-window match rule (spec 2.11 to 2.13).
   Acceptance tests 17 to 24.
4. `memory/__init__.py` with the export list in spec section 2, then the reader test
   (acceptance test 25) and the import-hygiene test (acceptance test 2).

Every test database lives under pytest's `tmp_path`; every timestamp comes from the
`frozen_clock` fixture in `tests/conftest.py`.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if `Database.migrate()` cannot take the memory schema
without changing more than the import and `executescript` lines, stop and flag rather than
restructuring `storage.py`. If the scan bounds (acceptance tests 23 and 24) cannot be met with the
prefix-window rule in spec 2.12, flag it with the timing rather than adding a dependency.
