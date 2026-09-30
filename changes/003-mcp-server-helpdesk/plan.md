# Plan: 003-mcp-server-helpdesk

grounded_at: `ee9f80f` (the `main` commit that merged the 002-memory-canary-guard build, PR #19,
2026-09-30; the planning thread checked the merged 002 interface against this spec and set this
value at ratification; the builder only checks it)

lane: claude

## Wall

wall_expected:
- `src/petasos/mcp/**`
- `src/petasos/helpdesk/**`
- `src/petasos/sessions/**`
- `src/petasos/app.py`
- `src/petasos/storage.py` (only to import `HELPDESK_SCHEMA` and `SESSIONS_SCHEMA` inside `migrate()`)
- `tests/test_app_*.py`
- `tests/test_mcp_*.py`
- `tests/test_helpdesk_*.py`
- `tests/test_sessions_*.py`
- `tests/test_storage_*.py` (only where a test enumerates the tables `migrate()` creates)
- `changes/003-mcp-server-helpdesk/report.md`
- `changes/003-mcp-server-helpdesk/tasks.md` (ticking boxes only)

wall_forbidden: everything standing-forbidden in AGENTS.md section 1. Do not touch
`src/petasos/__init__.py` (000's), `src/petasos/trust/**`, `src/petasos/ledger/**` (001's),
`src/petasos/memory/**` (002's), or `src/petasos/owner/**` (004's). If a 001 or 002
interface does not do what this spec needs, stop and flag; do not work around it by editing
those packages. The 001 review's should-fix items (`GrantStore.stage()` never sweeps, the
`already_executed` outcome code, `run_direct`'s version re-check) are not this change's.

The merge gate enforces this list mechanically from the copy of this file on `main`.

## Parallel-safety note

This change depends on 002 (guard, canaries, memory store) and cannot build alongside it.
Changes 004 (owner API and demo app) and 005 (deploy) both depend on this change and may
build in parallel with each other after it merges; their walls are disjoint from each other
(`src/petasos/owner/**` and `demo/**` for 004; `fly.toml`, `Dockerfile`, `.github`-adjacent
deploy files and `docs/` for 005). Under the strict up-to-date rule, whichever of them merges
second gets one "behind main" repair from the builder.

## Build order

1. `helpdesk/data.py`: `HELPDESK_SCHEMA`, the seed table, `seed_session`, scope-bound ticket
   reads and version bump helpers (spec 3.11 to 3.13, 3.18). Then `helpdesk/fake_email.py` and
   `helpdesk/fake_payments.py` (spec 3.17) and `helpdesk/tools.py`: `ReadCapture`,
   `build_registry(db, scope=, clock=, capture=)` with validators, resolvers, effects, and
   `current_version` as closures over the scope (spec 3.5a, 3.8, 3.14 to 3.17). Test the
   registry directly against a `Gate` before any HTTP exists: acceptance tests 9 (the tool
   half), 11, 12 (the capture half), 13 (the registry half), 25 (the tier and verb half).
2. `sessions/quotas.py` (`reserve`, `abuse_counters`, ceilings), `sessions/store.py`
   (`SESSIONS_SCHEMA`, `SessionStore.mint` as one `BEGIN IMMEDIATE` transaction, `expire`),
   `sessions/visitor.py` (`session_routes`, the `POST /session` handler) (spec 3.19 to 3.22).
   Then the two-line `storage.py` edit and the table-list test. Acceptance tests 21, 22, 23
   (store layer), 24 (store layer).
3. `mcp/identity.py` (`Identity`, `ClientRegistry`), `mcp/middleware.py` rewritten for the
   registry, the per-request identity in `scope["state"]`, and the session bindings (spec 3.1
   to 3.3), and `mcp/outcomes.py` (spec 3.24). Acceptance tests 1, 3, 4, and the sentences
   half of 25.
4. `mcp/tools.py` (`GateFactory`, `result_json`, `tool_result`, the adapters with typed
   parameters, the `ReadCapture` per call), the `ServerMiddleware` for tool visibility, the raw
   argument shape check, the call quota, and the `isError` refusal envelope, and
   `mcp/server.py`'s `build_mcp_server(db=, clock=)` extended to register every tool (spec 3.4
   to 3.8, 3.15, 3.21). Acceptance tests 2, 5, 6, 7, 8, 10, 14, 26, and the MCP halves of 9,
   12, 13, 23.
5. `mcp/guard_seam.py` (spec 3.9, 3.10: unit buffering, SSE framing, the live canary set, the
   409 first-unit path and the `event: message` later-unit termination, each tested through the
   real MCP client as well as recorded ASGI sends, the route label, the ledger row and counter)
   and `app.py`
   rewritten to `create_app(db, clock=)` with the middleware order in 3.23. Acceptance tests
   15 to 20, then the HTTP halves of 21 to 24, then 27, 28, and the reader test 29.
   `tests/test_app_mcp.py` from 000 is rewritten for the new `create_app` signature; its five
   checks (healthz, 401 before the handshake, no redirect, ping over streamable HTTP, clean
   lifespan) all stay, with the tool-list assertion changed from `== ["ping"]` to "contains
   `ping`" (an identity now sees more tools).

Every test database lives under pytest's `tmp_path`; every timestamp comes from the
`frozen_clock` fixture; HTTP tests use Starlette's `TestClient` and the pinned `mcp` client in
process, as `tests/test_app_mcp.py` already does; seam tests record every downstream ASGI
`send` message.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if the pinned `mcp` 2.2.0 `ServerMiddleware` does not
expose the HTTP request (`ctx.request`), cannot rewrite the `tools/list` result, or cannot
return a `CallToolResult` with `isError=true` for a refused `tools/call`, stop and flag with
the SDK source lines rather than downgrading to a lower-level server or patching the SDK. The SDK's argument model does not reject extra keys or coerced types (spec 3.15 says why);
the raw shape check in the `ServerMiddleware` is the strictness boundary, not something to
look for in the SDK. If scanning SSE events in the seam requires
buffering more than one event, or delaying `http.response.start` breaks the streamable
transport's session handshake, flag it with the observed behaviour rather than buffering a
stream or forwarding an unscanned unit. If any test needs a real clock or a sleep, flag it.
