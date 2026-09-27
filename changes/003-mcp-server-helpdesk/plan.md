# Plan: 003-mcp-server-helpdesk

grounded_at: (set by the planning thread after change 002 has merged, to the `main` commit
of that moment; the builder checks it and never fills it in. This change cannot be claimed
until 002-memory-canary-guard has merged.)

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

1. `helpdesk/data.py`: `HELPDESK_SCHEMA`, the seed table, `seed_session`, ticket reads and
   version bump helpers (spec 3.11 to 3.13, 3.18). Then `helpdesk/fake_email.py` and
   `helpdesk/fake_payments.py` (spec 3.17) and `helpdesk/tools.py`: validators, resolvers,
   effects, `build_registry` (spec 3.14 to 3.17). Test the registry directly against a `Gate`
   before any HTTP exists: acceptance tests 9, 10, 11, 23 (the tier and verb half).
2. `sessions/store.py` (`SESSIONS_SCHEMA`, `SessionStore.mint`, `expire`), `sessions/quotas.py`
   (`quota`, `abuse_counters`, ceilings), `sessions/visitor.py` (`session_routes`, the
   `POST /session` handler) (spec 3.19 to 3.22). Then the two-line `storage.py` edit and the
   table-list test. Acceptance tests 17 to 21 at the store level; the HTTP half after step 5.
3. `mcp/identity.py` (`Identity`, `ClientRegistry`), `mcp/middleware.py` rewritten for the
   registry and the per-request identity in `scope["state"]` (spec 3.1 to 3.3), and
   `mcp/outcomes.py` (spec 3.24). Acceptance tests 1, 3, 4, 23 (the sentences half).
4. `mcp/tools.py` (adapters, `result_json`, `GateFactory`), the `ServerMiddleware` for tool
   visibility and the per-session call quota, and `mcp/server.py` extended to register every
   tool (spec 3.4 to 3.8, 3.21). Acceptance tests 2, 5, 6, 7, 8, 22.
5. `mcp/guard_seam.py` (spec 3.9, 3.10) and `app.py` rewritten to `create_app(db, clock=)`
   with the middleware order in 3.23. Acceptance tests 12 to 16, then the HTTP halves of 17
   to 21, then 24, 25, and the reader test 26. `tests/test_app_mcp.py` from 000 is rewritten
   for the new `create_app` signature; its five checks (healthz, 401 before the handshake,
   no redirect, ping over streamable HTTP, clean lifespan) all stay.

Every test database lives under pytest's `tmp_path`; every timestamp comes from the
`frozen_clock` fixture; HTTP tests use Starlette's `TestClient` and the pinned `mcp` client,
as `tests/test_app_mcp.py` already does.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if the pinned `mcp` 2.2.0 `ServerMiddleware` does not
expose the HTTP request (`ctx.request`) or cannot rewrite the `tools/list` result, stop and
flag with the SDK source lines rather than downgrading to a lower-level server or
patching the SDK. If scanning SSE events in the seam requires buffering a whole stream,
flag it rather than buffering. If any test needs a real clock or a sleep, flag it.
