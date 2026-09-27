# Tasks: 003-mcp-server-helpdesk

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Write `helpdesk/data.py`: `HELPDESK_SCHEMA`, the seed constants (fictional only), `seed_session`,
      ticket reads, `current_version`, and the version-bump helper (spec 3.11 to 3.13, 3.18).
- [ ] Write `helpdesk/fake_email.py` and `helpdesk/fake_payments.py` (grant-keyed rows, never send or
      move anything; docstrings say so) and `helpdesk/tools.py` with the six `ToolDefinition`s and
      `build_registry` (spec 3.14 to 3.17). Test against a `Gate` directly. Satisfies acceptance
      tests 9, 10, 11, and the tier and verb half of 23.
- [ ] Write `sessions/store.py` (`SESSIONS_SCHEMA`, `SessionStore.mint`, `expire`), `sessions/quotas.py`
      (`quota`, `abuse_counters`, ceilings), and `sessions/visitor.py` (`session_routes`) (spec 3.19 to
      3.22); add both schemas to `Database.migrate()` and update the table-list test. Satisfies the
      store-level halves of acceptance tests 17 to 21.
- [ ] Write `mcp/identity.py` (`Identity`, `ClientRegistry.authenticate` with the dummy-digest compare),
      rewrite `mcp/middleware.py` for the registry, per-request identity, and the MCP-session binding,
      and write `mcp/outcomes.py` (spec 3.1 to 3.3, 3.24). Satisfies acceptance tests 1, 3, 4, and the
      sentences half of 23.
- [ ] Write `mcp/tools.py` (`GateFactory`, `result_json`, the adapters), the `ServerMiddleware` for tool
      visibility and the call quota, and extend `mcp/server.py` to register every tool (spec 3.4 to
      3.8, 3.21). Satisfies acceptance tests 2, 5, 6, 7, 8, 22.
- [ ] Write `mcp/guard_seam.py` (`GuardSeam`, `UNGUARDED_ROUTES`, chunk tail, JSON and SSE scanning, the
      409 and stream-abort paths, the ledger row) (spec 3.9, 3.10). Satisfies acceptance tests 12 to 16.
- [ ] Rewrite `app.py` to `create_app(db, clock=)` with the middleware order in 3.23 and `main` reading
      `PETASOS_DB` only under `__main__`; rewrite `tests/test_app_mcp.py` for the new signature keeping
      its five checks; update the package `__init__` exports (spec section 2, 3.23, 3.26). Satisfies the
      HTTP halves of acceptance tests 17 to 21, plus 24, 25, and the reader test 26.
- [ ] Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv pip check`,
      `uv run pytest`; freeze a commit; review the diff against every acceptance test; run the wall
      check; write `report.md`; open the pull request.
