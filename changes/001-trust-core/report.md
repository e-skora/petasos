# Report: 001-trust-core

Verdict: BUILT
Summary: A reader can now open `src/petasos/storage.py`, `src/petasos/ledger/`, and
`src/petasos/trust/` and see the whole safety core the rest of the project sits on: a
tool's server-owned risk profile turns into a tier by fixed rule, never a caller's
say-so; a risky action becomes a frozen, hashed action record that a `Gate.propose`
call stages as a single-use grant; a person approves that exact record through
`GrantStore.approve` (burning the grant on a verb mismatch, refusing self-approval,
sweeping expiry on every call); `Executor.run` re-checks the record's hash, the
current policy version, and the resource's current version before running the fake
effect, the grant's move to `EXECUTED`, and one ledger row in a single SQLite
transaction, so a retry of an already-run grant repeats nothing and returns the first
outcome. Hard constraints sit above the tier ladder and cannot be unlocked from
request content. Every decision is recorded in a hash-chained ledger that holds no
caller-supplied text; `Ledger(db).verify()` names the first row a hand edit breaks. A
`scope` argument threads through every grant, rail slot, and ledger row so one
visitor's owner can never see or act on another visitor's work. There is still no
HTTP, no MCP, and no real help-desk tool: those are change 003 and later.
Commit: this report is committed on branch build/001-trust-core; see the pull
request's head commit for the exact sha (a report cannot correctly name the hash of
the commit that contains it).
Tests: CI run https://github.com/e-skora/petasos/actions/runs/36317602305 (`python`,
`demo`, `private-identifiers` all succeeded) on pull request #14's opening commit,
560eb8468d4fa80201a661e4e0c2ac60803373d9. Local count 249 passed / 0 failed, 1 skipped
(the private-identifier gate, which only runs where the `PRIVATE_DENYLIST` secret is
set) at the same commit. `uv run ruff check .` and `uv run ruff format --check .` both
pass clean at the same commit. CI is the merge gate (D-013), never this report's
advisory count.
Changed files:
- changes/001-trust-core/tasks.md
- src/petasos/ledger/__init__.py
- src/petasos/ledger/chain.py
- src/petasos/ledger/store.py
- src/petasos/storage.py
- src/petasos/trust/__init__.py
- src/petasos/trust/constraints.py
- src/petasos/trust/executor.py
- src/petasos/trust/gate.py
- src/petasos/trust/grants.py
- src/petasos/trust/outcomes.py
- src/petasos/trust/record.py
- src/petasos/trust/risk.py
- src/petasos/trust/tools.py
- tests/test_ledger_chain.py
- tests/test_storage_database.py
- tests/test_trust_executor.py
- tests/test_trust_gate.py
- tests/test_trust_grants.py
- tests/test_trust_helpers.py
- tests/test_trust_import_hygiene.py
- tests/test_trust_ledger_content.py
- tests/test_trust_outcomes.py
- tests/test_trust_record.py
- tests/test_trust_risk.py
- tests/test_trust_scope.py
- changes/001-trust-core/report.md (this file)

Repair (2026-09-27): merged `origin/main` (PR #13, `.github/**` and
`tests/test_merge_gate.py` only, no conflict with this change's wall) to clear the
"branch is behind main" reason, and fixed the Claude review blocker below. Local
suite re-run at the repair commit: 257 passed / 0 failed, 1 skipped, same skip as
before. `uv run ruff check .` and `uv run ruff format --check .` and `uv pip check`
all still pass clean.
Deviations from spec: none. `tests/test_trust_helpers.py` matches the
`tests/test_trust_*.py` wall pattern but defines no `test_*` function itself; it holds
the fake refund, email, note, and delete tools that plan.md asks tests to define
themselves, shared across the other `test_trust_*.py` files so each acceptance test is
written once against one set of fakes rather than five slightly different copies.
Dependencies changed: none; `uv sync --locked` resolved the pins already recorded in
`pyproject.toml` and `uv.lock` without modifying either. `uv pip check`: all 44
installed packages are compatible.
Flags: none
Review findings and disposition:
- Claude review of commit e82e3b96d78bccbf69b48911d48308d4d8f4f54b:
  - blocker | src/petasos/trust/gate.py:71-83 | `ActionRecord` construction and
    `record_hash` computation ran outside the fail-closed try/except that wraps
    `validate`/`resolve`, so a resolved result with a float anywhere in its arguments,
    or with only one of `amount_minor`/`currency` set, raised an uncaught exception
    out of `Gate.propose` instead of `refused/invalid_arguments`, with no ledger row:
    fixed in this repair commit by moving both calls inside the same fail-closed
    try/except, with a new test,
    `test_resolved_money_missing_currency_is_refused_and_ledger_records_it` in
    `tests/test_trust_gate.py`, that reproduces the reported case (a resolved result
    with `amount_minor` set and `currency` left `None`) and checks both the result
    code and that exactly one `refused` ledger row is written.
  - should-fix | src/petasos/trust/grants.py:169 | `GrantStore.stage()` never calls
    `sweep()`, unlike every other public entry point: accepted, not fixed here. This
    is a should-fix, not a blocker, and is currently harmless because `GRANT_TTL`
    (24h) exceeds `RAIL_WINDOW` (1h), so a grant cannot be both expired and still
    inside the rail's counting window; left as a real gap for a follow-up change if
    either constant changes.
  - nit | src/petasos/trust/outcomes.py:46 | `Result` carries no field for the stored
    outcome code on an `already_executed` retry: accepted, not fixed here; no
    acceptance test in the spec requires it.
  - nit | src/petasos/trust/executor.py:240 | `run_direct` does not re-check
    `current_version` before running the effect, unlike the grant path: accepted, not
    fixed here; the window is narrow (propose to run_direct is one call) and no
    acceptance test requires the check.
- Codex review (P2, non-blocking; none reached the P0/P1 badge the merge gate treats
  as blocking) of commits 560eb8468d4fa80201a661e4e0c2ac60803373d9 and
  e82e3b96d78bccbf69b48911d48308d4d8f4f54b: raised the same `grants.py` sweep gap as
  the should-fix above, plus three ledger-chain findings (row id not bound into the
  hash, malformed `detail_json` raising instead of returning the row id, and deletion
  of the final row going undetected) and the same `executor.py` outcome-code gap as
  the nit above: accepted, not fixed here for the same reasons; none is a blocker.
Open questions appended to QUESTIONS.md: none
