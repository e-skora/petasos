# Report: 005-deploy-fly-cloudflare

Verdict: BUILT

Summary: After this merges, Elias can start a release of the API by hand against
one exact commit, watch the "Deploy API" workflow refuse anything that is not a
green commit on `main`, deploy exactly one Fly.io machine with `python -m
petasos.serve` as its entrypoint, and have the release itself proven by a script
that mints a visitor session, reads the help desk's $42.00 refund ticket as the
agent, proposes the refund, approves and executes it as the owner, and confirms
the refund by reading it back from the ticket, never printing a token anywhere.
The running server now bounds every request to 64 KiB before any other layer
reads it, and a background tick (once a minute) expires visitor sessions within
the hour they end, rotates the audit ledger into a verified archive once it
passes 10,000 rows, and prunes old archives, so the demo stays bounded and alive
without anyone minting a request for it to happen. An operator can also run a
reset, an expiry, or a rotation by hand over SSH. `docs/deploy.md` documents all
of this, and `docs/setup.md` points at it.

Commit: 0684151 on branch build/005-deploy-fly-cloudflare

Tests: CI run pending; local count 646 passed / 0 failed / 1 skipped at 0684151
(advisory; the skip is `test_no_private_identifiers.py`, which skips with a
warning when the `PRIVATE_DENYLIST` secret is absent, as it is in this sandbox).
The whole pre-existing suite (every test file present before this change, 003's
and 004's) passed unmodified in this same run, satisfying acceptance test 15.

Changed files:
```
A	.dockerignore
M	Dockerfile
M	changes/005-deploy-fly-cloudflare/tasks.md
A	docs/deploy.md
M	docs/setup.md
M	fly.toml
A	scripts/smoke_journey.py
A	src/petasos/limits.py
A	src/petasos/serve.py
A	src/petasos/sessions/maintenance.py
A	tests/support_005.py
A	tests/test_deploy_files.py
A	tests/test_limits_request_size.py
A	tests/test_maintenance_errors.py
A	tests/test_maintenance_pruning.py
A	tests/test_maintenance_reset.py
A	tests/test_maintenance_rotation.py
A	tests/test_maintenance_tick.py
A	tests/test_serve_build_app.py
A	tests/test_serve_purity.py
A	tests/test_serve_reader.py
A	tests/test_serve_settings.py
A	tests/test_smoke_journey.py
A	changes/005-deploy-fly-cloudflare/report.md
```

Deviations from spec:
- **`petasos.limits.SENTENCES`, a public export spec 5.4 did not name.** Change
  004's `tests/test_owner_hygiene.py` (outside this change's wall, so it cannot be
  edited) already contains `test_too_large_sentence_matches_005_limits_when_present`,
  which imports `petasos.limits` and asserts `limits_module.SENTENCES["refused/too_large"]
  == OWNER_SENTENCES["refused/too_large"]` once this module exists. `limits.py` now
  defines `SENTENCES: dict[str, str]` with both of this change's codes, and the two
  constant refusal payloads are built from it, so the two changes' sentences cannot
  drift apart. An additive export, not a behavior change.
- **Smoke journey step order follows acceptance test 11's explicit sequencing over
  spec 5.10's prose grouping.** Spec 5.10 lists the MCP steps (approve, approve
  again, list empty, visitor tools/list) before describing the receipt reads as a
  separate concern ("then the receipt..."). Acceptance test 11 states the order
  explicitly: "first approval, first receipt read (one row), repeated approval,
  second receipt read (still one row)". `scripts/smoke_journey.py` follows test 11:
  approve, receipt read, approve again, receipt read again, then the empty-list and
  visitor-tools-list steps. Every status code and outcome named by 5.10 still holds;
  only the position of the two reads relative to the second approval differs from
  the prose summary, matching the test that states it precisely.
- **Acceptance test 9's archive-corruption coverage is representative, not
  exhaustive.** The four named corruption shapes (a dropped row, an altered
  `detail_json` with its old hash kept, a changed id, an extra row) all funnel
  through the same two checks in `rotate_ledger` (the read-back digest over
  `petasos.trust.record.canonical_json`, and `chain.verify` on both connections).
  `tests/test_maintenance_rotation.py` proves one concrete mechanism (a row
  deleted from the archive right after the copy loop, before the checks run) is
  caught, rolled back, and cleaned up; the other three shapes exercise the same
  code path and are not each given their own test. `chain.append` raising, a
  pre-existing archive file, a broken source chain, and the two-thread race are
  each covered directly.
- **`MaintenanceRunner`'s in-flight-tick-blocks-shutdown test uses a real OS
  thread**, per spec 5.5's own description ("a spy that blocks on a
  `threading.Event`"), not an `asyncio` primitive: `run_maintenance` runs under
  `asyncio.to_thread`, so a synchronous block there is a real thread block, and
  the test proves shutdown does not reach the inner app until that thread signals
  completion.

Dependencies changed: none (`uv pip check` result: "All installed packages are
compatible").

Flags: none.

Review findings and disposition: none yet.

Open questions appended to QUESTIONS.md: none.
