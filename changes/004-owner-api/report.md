# Report: 004-owner-api

Verdict: BUILT

Summary: After this merges, a person with the three tokens `POST /session` returns
can use plain `curl` calls under `/owner/` to do everything the MCP tools already
let an assistant do, through the same trust gate, grant store, executor, and
ledger. A browser (or `curl`) can see who it is signed in as, list and read
tickets, propose a reply, a refund, or a deletion as the agent, see the exact
staged request as a plain-language approval card as the owner, approve or abort
it, read the resulting ticket activity, and read the session's ledger in plain
sentences, including a working "verify the chain" call. The two front doors (MCP
and the browser) now share one call-logic module, so they cannot drift apart. A
browser page served from an allowed origin gets the cross-origin headers it
needs; every other origin, and every other endpoint, is unaffected.

Commit: 9c04176 on branch build/004-owner-api

Tests: CI run pending; local count 566 passed / 0 failed / 1 skipped at 9c04176
(advisory; the skip is `test_no_private_identifiers.py`, which skips with a
warning when the `PRIVATE_DENYLIST` secret is absent, as it is in this sandbox).
The whole 003 suite (every test file present before this change) passed
unmodified in this same run, satisfying acceptance test 16.

Changed files:
```
M	changes/004-owner-api/tasks.md
M	src/petasos/app.py
M	src/petasos/mcp/guard_seam.py
M	src/petasos/mcp/server.py
A	src/petasos/mcp/service.py
M	src/petasos/mcp/tools.py
A	src/petasos/owner/__init__.py
A	src/petasos/owner/api.py
A	src/petasos/owner/auth.py
A	src/petasos/owner/cards.py
A	src/petasos/owner/ledger_view.py
A	src/petasos/owner/origins.py
A	src/petasos/owner/outcomes.py
A	src/petasos/owner/reads.py
A	tests/test_mcp_service.py
A	tests/test_owner_api.py
A	tests/test_owner_fixtures.py
A	tests/test_owner_hygiene.py
A	tests/test_owner_ledger_view.py
A	tests/test_owner_origins.py
A	changes/004-owner-api/report.md
```

Deviations from spec:
- **`POST /owner/ledger/verify`'s body contract.** Spec 4.5 states explicitly that
  `POST /owner/abort-all`'s body must be "absent or `{}`", but does not restate
  the same rule for `POST /owner/ledger/verify`, which also names no keys. I
  applied the same "absent or `{}`" contract to `ledger/verify` by analogy, since
  it is the only reading consistent with the general body-shape rule in 4.4 (a
  body must parse with exactly the endpoint's named keys; an endpoint that names
  none still needs some rule for a present-but-empty body). An ordinary
  implementation choice within what the spec already implies, not a decision that
  reopens anything settled.
- **Fixture contract coverage (acceptance test 17).** `demo/package.json` does
  not exist yet (change 007 has not built), so fixtures are not mandatory this
  run. `tests/test_owner_fixtures.py` drives the full app through all 34 named
  scenarios in `FIXTURES`, redacts every dynamic value (ids, timestamps, tokens,
  the session id wherever it appears, including embedded in a customer name or
  email), and writes the result as a reference set under `tmp_path`; it does not
  compare against committed fixtures, because none exist to compare against. The
  comparison checker itself (key set, JSON type, exact value for `status`,
  `verb`, `tier`, `explanation`, and non-emptiness) is unit-tested directly
  against synthetic fixtures covering every failure mode the spec names. The
  byte-for-byte path against real committed fixtures under `demo/src/fixtures/`
  will only run once change 007 commits them.
- **Test coverage is representative, not exhaustive,** in the same places 003's
  report already named: the per-session rolling quota window (three `reserve`
  calls admit, the fourth refuses, the window clears after it passes) is proved
  generically at the store layer by 003's existing `tests/test_sessions_quotas.py`
  and is not re-proved from scratch here; this change's own tests exercise the
  HTTP-layer effect (a 429 past the limit) for each of the three owner-API quota
  families instead. `GrantStore`'s compare-and-swap concurrency guarantee is
  likewise proved generically, outside this change, by `tests/test_trust_scope.py`.

Dependencies changed: none (`uv pip check` result: "All installed packages are
compatible").

Flags: none.

Review findings and disposition: none yet; this is the first build commit, and
both reviews run against it after the pull request opens.

Open questions appended to QUESTIONS.md: none.
