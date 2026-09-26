# Tasks: 001-trust-core

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Write `storage.py`: `Database(path)`, `connect()`, `transaction()`, `migrate()` (spec
      1.9). Satisfies acceptance test 38 (the storage half).
- [ ] Write `ledger/store.py` (schema and the docstring in spec 1.23) and `ledger/chain.py`
      (`append` inside the caller's transaction, `verify`). Satisfies acceptance tests 34, 35.
- [ ] Write `trust/risk.py`: `RiskProfile`, `InvalidProfile`, `Tier`, `derive_tier`,
      `verb_for`, `rail_for`, `POLICY_VERSION`. Add the hypothesis monotonicity test and the
      17-row expected table test. Satisfies acceptance tests 1, 2, 3, 4.
- [ ] Write `trust/record.py`: `ActionRecord`, `canonical_json`, `record_hash`, with the
      one-field-differs test for every field and the float refusal. Satisfies acceptance
      test 6.
- [ ] Write `trust/tools.py`: `ToolDefinition`, `ToolRegistry` (duplicate names refused),
      `Resolved`, `InvalidArguments`, `NotFound`.
- [ ] Write `trust/outcomes.py` with a sentence for every result code in spec section 1.
      Satisfies acceptance test 37.
- [ ] Write `trust/constraints.py`. Satisfies acceptance tests 30, 31 once the gate exists.
- [ ] Write `trust/grants.py`: staging with rail reservation, the two grant views, and
      `list_pending(viewer)`. Satisfies acceptance tests 7, 15, 18.
- [ ] Add approve exactly as spec 1.12 describes, with its tests. Satisfies acceptance tests
      8, 9, 10, 11, 12.
- [ ] Add the expiry sweep (spec 1.14) with the boundary-instant tests. Satisfies acceptance
      tests 13, 14.
- [ ] Add abort and `abort_all` with slot release (spec 1.13, 1.15). Satisfies acceptance
      tests 16, 17, 28.
- [ ] Write `trust/executor.py`: `Executor.run(grant_id)` in one transaction (spec 1.16).
      Satisfies acceptance tests 19 to 27.
- [ ] Write `trust/gate.py`: `Gate.propose` and direct actions (spec 1.2, 1.5, 1.17, 1.19).
      Satisfies acceptance tests 5, 29, 30, 31, 32, 33.
- [ ] Add the ledger-content scan over every row the suite wrote. Satisfies acceptance
      test 36.
- [ ] Add the import-hygiene test for `petasos.trust`, `petasos.ledger`, `petasos.storage`.
      Satisfies acceptance test 38 (the import half).
- [ ] Write `changes/001-trust-core/report.md` per AGENTS.md section 5.
      Check: `grep -nE '<!--|TODO|TBD|\[fill' changes/001-trust-core/report.md` returns
      nothing.
