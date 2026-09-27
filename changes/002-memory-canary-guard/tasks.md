# Tasks: 002-memory-canary-guard

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Write `memory/store.py`: `MemoryTier` (ordered), `Entry`, `InvalidEntry`, `TierLocked`,
      `MEMORY_SCHEMA`, and `MemoryStore(db, scope=, clock=)` with `put`, `put_in`, `read`,
      `search`, `sweep` (spec 2.1 to 2.5, 2.7 to 2.10). Satisfies acceptance tests 3 to 8, 11, 12,
      13, 14 (canary planting in `put` lands with the next task).
- [ ] Add `MEMORY_SCHEMA` to `Database.migrate()` in `storage.py` (import at call time, like
      the ledger and grants schemas) and update the table-enumerating storage test.
      Satisfies acceptance test 2 (the migrate half).
- [ ] Write `memory/canary.py`: `mint_canary`, `all_canaries`, `canary_entry`, and wire
      minting and re-planting into `MemoryStore.put` (spec 2.5, 2.6, 2.14). Satisfies
      acceptance tests 4, 5, 6, 7, 9, 10.
- [ ] Write `memory/guard.py`: `Hit`, `GuardTripped`, `scan`, `assert_no_private_payload`
      with the depth, size, type, and exception bounds (spec 2.11 to 2.13). Satisfies
      acceptance tests 15 to 20.
- [ ] Write `memory/__init__.py` with exactly the exports in spec section 2, the
      import-hygiene test (acceptance test 1 and the import half of 2), and the reader test
      (acceptance test 21).
- [ ] Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
      `uv pip check`, `uv run pytest`; freeze a commit; review the diff against every
      acceptance test; run the wall check; write `report.md`; open the pull request.
