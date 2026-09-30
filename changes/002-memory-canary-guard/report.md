# Report: 002-memory-canary-guard

Verdict: BUILT
Summary: A reader can now plant a fact in `petasos.memory` at a tier from T0 (public)
to T5 (private-local), and every fact stored at T4 or above gets one hidden canary
token, planted once in its text and never re-minted, even across many later updates
of the same key. A destination-blind guard, `assert_no_private_payload`, scans any
outgoing payload (nested dictionaries, lists, tuples, strings, and bytes) for a
planted canary or a field named `private` or `private_*`, and raises on a hit no
matter who is asking, exactly as `ARCHITECTURE.md` invariants 12 and 13 require. A
broken guard (a bad payload shape, an oversized payload, an object it cannot read)
also aborts rather than serving the response (invariant 14). Lowering a key that
already carries a canary below T4 is refused outright, so private text can never
leave without its marker. A canary outlives the entry it was planted in: even after
the entry expires and is swept away, or is overwritten by a fresh entry with a new
id, the old token stays registered and still trips the guard. There is still no MCP
server, no help-desk tool, and no demo app wiring this guard to a real outgoing
response: that is change 003.
Commit: e3b7b68416609669973c3232ac51cf7833b8a4ed on branch build/002-memory-canary-guard
Tests: CI run pending; see pull request checks for commit
e3b7b68416609669973c3232ac51cf7833b8a4ed. Local count 316 passed / 0 failed, 1
skipped (the private-identifier gate, which only runs where the `PRIVATE_DENYLIST`
secret is set) at the same commit. `uv run ruff check .` and `uv run ruff format
--check .` both pass clean at the same commit. `uv pip check` reports all installed
packages compatible. CI is the merge gate (D-013), never this report's advisory
count.
Changed files:
- changes/002-memory-canary-guard/report.md (this file)
- changes/002-memory-canary-guard/tasks.md
- src/petasos/memory/__init__.py
- src/petasos/memory/canary.py
- src/petasos/memory/guard.py
- src/petasos/memory/store.py
- src/petasos/storage.py
- tests/test_memory_canary.py
- tests/test_memory_guard.py
- tests/test_memory_import_hygiene.py
- tests/test_memory_reader.py
- tests/test_memory_store.py
- tests/test_storage_database.py
Deviations from spec: none.
Dependencies changed: none. `uv pip check` result: all installed packages compatible.
Flags: none.
Review findings and disposition:
- blocker | src/petasos/memory/guard.py:88-111 | Dict keys that are not str/bytes
  (e.g. a tuple or frozenset) were silently skipped by the guard's scan, never
  scanned for a canary and never routed to the guard_error path, letting a canary
  hidden in a dict key escape detection: fixed in e3b7b68416609669973c3232ac51cf7833b8a4ed
  (unsupported key types now raise, landing in the same guard_error path as
  unsupported value types).
- should-fix | src/petasos/memory/guard.py:103-107 | Bytes dict keys were scanned
  for canaries but never checked against the private-key label, unlike str keys:
  fixed in e3b7b68416609669973c3232ac51cf7833b8a4ed (bytes keys are decoded once and
  checked against the same private-key rule as str keys).
- nit | tests/test_memory_canary.py:150 | The put_in-based test for acceptance
  test 9 only exercised the rollback path, never the successful-commit outcome:
  fixed in e3b7b68416609669973c3232ac51cf7833b8a4ed (added a parametrized test
  mirroring the put()-based one, asserting the new id, tier, fresh canary, and
  live old token for a committed put_in overwrite).
Open questions appended to QUESTIONS.md: none.
