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
Commit: 266dd4fde5ce07c02bb8e5c58aa67bb91f07d80f on branch build/002-memory-canary-guard
Tests: CI run pending; see pull request checks for commit
266dd4fde5ce07c02bb8e5c58aa67bb91f07d80f. Local count 318 passed / 0 failed, 1
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
- blocker (Codex P1) | src/petasos/memory/store.py:120-124 | `_sweep_in` compared
  `expires_at` and the current time as raw ISO-8601 text in SQL; a stored
  `expires_at` and a `now` with different UTC offsets for the same instant do not
  compare correctly as strings, so an already-expired entry could survive a sweep:
  fixed in 266dd4fde5ce07c02bb8e5c58aa67bb91f07d80f (both the stored
  `expires_at` and the value compared against in `_sweep_in` are now
  normalized to UTC before formatting, so the text comparison matches
  chronological order regardless of the caller's offset; a new test,
  `test_sweep_compares_expiry_across_differing_utc_offsets`, fails without the
  fix and passes with it).
- P2 (Codex) | tests/test_memory_guard.py:94 | The unsupported-type test called
  the real clock (`datetime.now(UTC)`) instead of a fixed value, against the
  repo rule that tests use a fake clock: fixed in
  266dd4fde5ce07c02bb8e5c58aa67bb91f07d80f (replaced with a fixed aware
  `datetime`).
- P2 (Codex) | tests/test_memory_canary.py:217 | The marker round-trip test only
  covered `put`, not `put_in`, though acceptance test 10 covers both: fixed in
  266dd4fde5ce07c02bb8e5c58aa67bb91f07d80f (added
  `test_reading_a_t4_entry_and_putting_its_text_back_through_put_in_stores_one_marker`,
  the same assertions run through `put_in` inside a caller transaction).
- P2 (Codex) | src/petasos/memory/guard.py:118 and the should-fix `blocker` and
  `should-fix` inline comments carried forward from GitHub on
  src/petasos/memory/guard.py:108 and :121 | These comments' `original_commit_id`
  is 683e18227b3bb228e3d83840b2abcb9aa98172c2 (the commit before the first
  repair): GitHub carries old inline comments forward onto unchanged lines of a
  new commit. Both were already addressed by the first repair
  (e3b7b68416609669973c3232ac51cf7833b8a4ed), logged above; refuted as findings
  against this head commit because they are not new.
Open questions appended to QUESTIONS.md: none.
