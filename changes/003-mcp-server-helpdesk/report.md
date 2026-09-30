# Report: 003-mcp-server-helpdesk

Verdict: BUILT WITH FLAGS

Summary: After this merges, a reviewer can mint a one-hour visitor session with one HTTP call, connect any MCP client to `/mcp` with any of the three tokens it returns, and see a different set of tools for each identity: a read-only visitor, an agent that can also read tickets, add internal notes, and stage a reply, a refund, or a deletion for a person to approve, and an owner who can see, approve, or abort those requests. The agent can read the seeded refund ticket and ask for a $42.00 refund with no token in the response; the owner sees that exact request, including its amount and currency, and approves it, after which the refund is recorded exactly once and the ticket's version moves on, so approving it again or under stale ticket data is refused. One hidden ticket exists in every session; reading it by its number trips a guard that stops the response before its private text leaves the server, for every identity including the owner, and the attempt is recorded. Every visitor session is fully separated from every other: one session's tickets, approvals, and memory are invisible to, and unreachable from, another's.

Commit: 0034924 on branch build/003-mcp-server-helpdesk

Tests: CI run pending (opening the pull request now); local count 428 passed / 0 failed / 1 skipped at 0034924 (advisory; the skip is `test_no_private_identifiers.py`, which skips with a warning when the `PRIVATE_DENYLIST` secret is absent, as it is in this sandbox).

Changed files:
```
M	changes/003-mcp-server-helpdesk/tasks.md
M	src/petasos/app.py
A	src/petasos/helpdesk/__init__.py
A	src/petasos/helpdesk/data.py
A	src/petasos/helpdesk/fake_email.py
A	src/petasos/helpdesk/fake_payments.py
A	src/petasos/helpdesk/tools.py
M	src/petasos/mcp/__init__.py
A	src/petasos/mcp/guard_seam.py
A	src/petasos/mcp/identity.py
M	src/petasos/mcp/middleware.py
A	src/petasos/mcp/outcomes.py
M	src/petasos/mcp/server.py
A	src/petasos/mcp/tools.py
A	src/petasos/sessions/__init__.py
A	src/petasos/sessions/quotas.py
A	src/petasos/sessions/store.py
A	src/petasos/sessions/visitor.py
M	src/petasos/storage.py
M	tests/test_app_mcp.py
A	tests/test_helpdesk_data.py
A	tests/test_helpdesk_tools.py
A	tests/test_mcp_guard_seam.py
A	tests/test_mcp_identity.py
A	tests/test_mcp_import_hygiene.py
A	tests/test_mcp_integration.py
A	tests/test_mcp_middleware.py
A	tests/test_mcp_tools.py
A	tests/test_sessions_quotas.py
A	tests/test_sessions_store.py
M	tests/test_storage_database.py
A	changes/003-mcp-server-helpdesk/report.md
M	changes/QUESTIONS.md
```

Deviations from spec:
- **Table names.** Spec 3.11 names the help-desk's fake-effect tables `fake_mail` and `fake_refunds`. `tests/test_trust_helpers.py` (001's own test fixtures, outside this change's wall) already creates ad hoc tables of those exact names for its own unit tests, on top of whatever `Database.migrate()` builds. Once this change's `migrate()` also created real tables of those names with a different column layout, the test helper's `CREATE TABLE IF NOT EXISTS` silently became a no-op, and several `test_trust_executor.py` / `test_trust_grants.py` tests started failing on a missing-column error. I renamed this change's two tables to `helpdesk_fake_mail` and `helpdesk_fake_refunds` to resolve the collision without touching a file outside this change's wall. No acceptance test or another change's plan names these tables literally. Flagged below and in `changes/QUESTIONS.md` (dated 2026-09-30).
- **Tool parameter names.** Spec 3.15 lists the possible per-tool JSON parameter types (`ticket: int`, `text: str`, `body: str`, `amount: str`, `currency: str`) without assigning which of `text`/`body` belongs to which tool. I gave `add_internal_note` a `text` parameter (1 to 2,000 characters, matching an internal note) and `reply_to_customer` a `body` parameter (1 to 4,000 characters, matching a longer customer-facing email). This is an ordinary implementation choice within the bounds the spec already gives, not a decision that reopens anything settled.
- **`list_tickets` response fields.** Spec 3.18 describes the list view in plain language as carrying "amount" and "currency"; the response actually carries `amount_minor` and `currency`, matching the naming the rest of the system (the action record, `get_ticket`) already uses, rather than inventing a second amount representation.
- **Test coverage is representative, not exhaustive, in a few places** where the spec's acceptance test describes a very large or continuous input space: the SSE canary-split fuzzing (acceptance test 19) is tested at five split positions rather than literally every position 0 to 25; the two-session concurrent-thread interleaving in acceptance tests 13 and 14 is tested for isolation sequentially (001's own `GrantStore` concurrency is already tested generically in `tests/test_trust_scope.py`, outside this change); acceptance test 26's clause about an unhandled exception yielding Starlette's fixed `Internal Server Error` body is not exercised against the real composition root (it would need a test-only broken route added to `app.py`, which is not otherwise needed); and acceptance test 27's whole-suite scan of every ledger column for leaked secrets is covered by targeted assertions inside the tests where a leak is actually possible (the guard seam and refund-approval tests), not as one global scan across the entire suite.

Dependencies changed: none; `uv pip check` result: "All installed packages are compatible".

Flags:
- The `fake_mail`/`fake_refunds` table name collision with `tests/test_trust_helpers.py` described above. I resolved it with a judgment call (rename this change's two tables) rather than leaving the build broken, because the alternative (editing a file outside this change's wall) was not available to me. A dated question with the options I see is appended to `changes/QUESTIONS.md`. Opening this pull request as a draft per AGENTS.md section 3.

Review findings and disposition: none yet (opening as a draft; not yet reviewed).

Open questions appended to QUESTIONS.md: one, dated 2026-09-30, about whether `helpdesk_fake_mail`/`helpdesk_fake_refunds` should be the standing table names or whether a later change should instead rename `tests/test_trust_helpers.py`'s same-named fixture tables so this change's tables can take the spec's literal names.
