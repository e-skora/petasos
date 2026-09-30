# Tasks: 003-mcp-server-helpdesk

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [x] Write `helpdesk/data.py`: `HELPDESK_SCHEMA`, the seed constants (fictional only; texts carry the
      scope so two sessions differ), `seed_session`, scope-bound ticket reads, `current_version`, and the
      version-bump helper (spec 3.11 to 3.13, 3.18).
- [x] Write `helpdesk/fake_email.py` and `helpdesk/fake_payments.py` (grant-keyed rows, never send or
      move anything; docstrings say so) and `helpdesk/tools.py` with `ReadCapture`, the six
      `ToolDefinition`s as closures over the scope and the capture, and `build_registry` (spec 3.5a,
      3.8, 3.14 to 3.17). Test against a `Gate` directly. Satisfies acceptance tests 11, the capture
      half of 12, the registry half of 13, the tool half of 9, and the tier and verb half of 25.
- [x] Write `sessions/quotas.py` (`reserve` inside the caller's transaction, `abuse_counters`, ceilings),
      `sessions/store.py` (`SESSIONS_SCHEMA`, `SessionStore.mint` as one transaction, `expire`), and
      `sessions/visitor.py` (`session_routes`) (spec 3.19 to 3.22); add both schemas to
      `Database.migrate()` and update the table-list test. Satisfies the store-level halves of
      acceptance tests 21 to 24.
- [ ] Write `mcp/identity.py` (`Identity`, `ClientRegistry.authenticate` with the dummy-digest compare),
      rewrite `mcp/middleware.py` for the registry, per-request identity, and the session bindings
      (absent binding refused; removed on DELETE, expiry, eviction), and write `mcp/outcomes.py`
      (spec 3.1 to 3.3, 3.24). Satisfies acceptance tests 1, 3, 4, and the sentences half of 25.
- [ ] Write `mcp/tools.py` (`GateFactory.for_call`, `result_json`, `tool_result`, the typed adapters, one
      `ReadCapture` per call), the `ServerMiddleware` for tool visibility, the raw argument shape check,
      the call quota, and the `isError` refusal envelope, and extend `build_mcp_server(db=, clock=)` in
      `mcp/server.py` to register every tool (spec 3.4 to 3.8, 3.15, 3.21).
      Satisfies acceptance tests 2, 5, 6, 7, 8, 10, 14, 26, and the MCP halves of 9, 12, 13, 23.
- [ ] Write `mcp/guard_seam.py` (`GuardSeam(app, db, clock)`, `UNGUARDED_ROUTES`, one-unit buffering with
      the byte limits, SSE framing (CRLF and LF, comment events, zero-unit streams), the live canary set, the 409 first-unit path and the `event: message` later-unit termination (both
      tested through the real MCP client: error code, sentence, and `error.data.status`), the route label,
      the ledger row and counter in one transaction, the no-recursion failure path) (spec 3.9, 3.10).
      Satisfies acceptance tests 15 to 20.
- [ ] Rewrite `app.py` to `create_app(db, clock=)` with the middleware order in 3.23 and `main` reading
      `PETASOS_DB` only under `__main__`; rewrite `tests/test_app_mcp.py` for the new signature keeping
      its five checks (tool list "contains `ping`"); update the package `__init__` exports (spec section 2, 3.23, 3.26). Satisfies the
      HTTP halves of acceptance tests 21 to 24, plus 27, 28, and the reader test 29.
- [ ] Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv pip check`,
      `uv run pytest`; freeze a commit; review the diff against every acceptance test; run the wall
      check; write `report.md`; open the pull request.
