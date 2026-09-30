# Tasks: 002-memory-canary-guard

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Write `memory/store.py`: `MemoryTier` (ordered), `Entry`, `InvalidEntry`, `TierLocked`,
      `MEMORY_SCHEMA`, and `MemoryStore(db, scope=, clock=)` with `put`, `put_in`, `read`,
      `search`, `sweep`, `_sweep_in`, with the one expiry rule for `put` and `put_in` (spec 2.1
      to 2.5, 2.7 to 2.10). Satisfies acceptance tests 3, 8, 13 to 16, and the `TierLocked` and
      expiry halves of 7 and 9 (canary minting and planting land with the canary task).
- [ ] Add `MEMORY_SCHEMA` to `Database.migrate()` in `storage.py` (import at call time, like
      the ledger and grants schemas) and update the table-enumerating storage test.
      Satisfies acceptance test 2 (the migrate half).
- [ ] Write `memory/canary.py`: `mint_canary`, `CanarySet`, `canary_set`, `all_canaries`,
      `canary_entry`, and wire minting, marker stripping, and re-planting into `put_in` (spec
      2.5, 2.6, 2.14). Satisfies acceptance tests 4 to 7, 9 to 12.
- [ ] Write `memory/guard.py`: `Hit`, `GuardTripped`, `scan`, `assert_no_private_payload`
      with the node, byte, depth, type, and exception bounds and the prefix-window match
      (spec 2.11 to 2.13). Satisfies acceptance tests 17 to 24.
- [ ] Write `memory/__init__.py` with exactly the exports in spec section 2, the
      import-hygiene test (acceptance test 1 and the import half of 2), and the reader test
      (acceptance test 25).
- [ ] Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
      `uv pip check`, `uv run pytest`; freeze a commit; review the diff against every
      acceptance test; run the wall check; write `report.md`; open the pull request.
