# Tasks: 001-trust-core

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Write `trust/manifest.py`: the `Manifest` dataclass with the fields in spec 1.1, no
      tier field. Satisfies acceptance test 1.
- [ ] Write `ledger/store.py` (SQLite schema, documenting which tables are chained) and
      `ledger/chain.py` (`append`, `verify`). Satisfies acceptance tests 12, 13, 15.
- [ ] Write `trust/tiers.py`: `derive_tier`, pure and monotone. Add the hypothesis-based test
      that setting any one risk flag from False to True never lowers the tier. Satisfies
      acceptance test 2. This is the task DECISIONS.md D-003 and ARCHITECTURE.md section 8
      mean when they say hypothesis is used here.
- [ ] Write `trust/verbs.py`: the verb table and `verb_for`, plus the test enumerating every
      verb the table can produce and confirming each has a registered executor. Satisfies
      acceptance test 3.
- [ ] Write `trust/grants.py`: `GrantStore(path, approvers, clock)` and `stage`, storing
      `manifest_json` and `manifest_hash` (spec 1.18) on the grant, with `Grant.for_proposer()`
      / `Grant.for_approver()` (spec 1.19) and `list_pending(viewer_is_approver)`. Satisfies
      acceptance tests 4 and 17.
- [ ] Add `GrantStore.approve` exactly as spec 1.8 describes: approver check first (spec 1.17),
      then one `BEGIN IMMEDIATE` transaction with the compare-and-swap `UPDATE`, the partial
      unique index on `(verb, token) WHERE state='AWAITING'`, and the concurrent-approval test.
      Satisfies acceptance test 5.
- [ ] Add the verb-mismatch burn (spec 1.8 step 2) and its two tests: the plain burn, and the
      right-verb versus wrong-verb race. Satisfies acceptance tests 6 and 16.
- [ ] Add the non-approver refusal path (grant untouched, later approval by an approver still
      succeeds) and its test. Satisfies acceptance test 7.
- [ ] Add the 24-hour TTL, the expiry sweep on `list` and `approve`, and the frozen-clock test
      that advances time past expiry. Satisfies acceptance test 8.
- [ ] Add the per-verb-family rate rails (`rail_for`, used by both `stage` and `approve`) and
      the six-in-an-hour test. Satisfies acceptance test 9.
- [ ] Write `trust/executor.py`: `Executor.run`, re-hashing the manifest and refusing on
      mismatch, with its test. Satisfies acceptance test 10.
- [ ] Write `trust/constraints.py`: the hard-constraint deny list, checked before tier
      derivation, with the `disable_gate`-denied-at-every-tier test. Satisfies acceptance
      test 11.
- [ ] Add the outcome-sentence table (a fixed dict, result code to plain sentence) and the
      test proving every result code has a sentence and no sentence contains a code or an id.
      Record in `report.md` which module the table lives in. Satisfies acceptance test 14.
- [ ] Add the import-hygiene test: importing `petasos.trust` and `petasos.ledger` touches no
      network, no environment variable, and no file outside a temporary directory. Satisfies
      acceptance test 15.
- [ ] Write `changes/001-trust-core/report.md` per AGENTS.md section 5.
      Check: `grep -nE '<!--|TODO|TBD|\[fill' changes/001-trust-core/report.md` returns
      nothing.
