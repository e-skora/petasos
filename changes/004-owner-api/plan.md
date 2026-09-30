# Plan: 004-owner-api

grounded_at: (set by the planning thread at ratification, after the 003 build merges; the
builder only checks it)

lane: claude

## Wall

The merge gate reads one path per bullet, on one line. Notes on what an edit may do follow
the list.

wall_expected:
- `src/petasos/owner/**`
- `src/petasos/mcp/service.py`
- `src/petasos/mcp/tools.py`
- `src/petasos/mcp/server.py`
- `src/petasos/mcp/guard_seam.py`
- `src/petasos/app.py`
- `tests/test_owner_*.py`
- `tests/test_mcp_service.py`
- `changes/004-owner-api/report.md`
- `changes/004-owner-api/tasks.md`

Allowed edits to the 003 files above, and nothing else in them: `mcp/tools.py` and
`mcp/server.py` only to move `TOOL_ARG_SPEC`, `ALLOWED_TOOLS`, the raw shape check, the card
builder, and approve-then-run into `mcp/service.py` and import them back under their old names
with an explicit `__all__` (spec 4.8; no behaviour change); `mcp/guard_seam.py` only
`_route_label` (add `owner` for paths starting with `/owner/`); `app.py` only the `OriginGate`
as the outermost layer, the owner routes, `redirect_slashes=False`, and the three `None` docs
URLs (spec 4.10; the `create_app` signature stays `(db, *, clock)`). `tasks.md`: ticking
boxes only.

wall_forbidden: everything standing-forbidden in AGENTS.md section 1, plus:
- `src/petasos/__init__.py`
- `src/petasos/trust/**`
- `src/petasos/ledger/**`
- `src/petasos/memory/**`
- `src/petasos/helpdesk/**`
- `src/petasos/sessions/**`
- `src/petasos/storage.py`
- `src/petasos/mcp/identity.py`
- `src/petasos/mcp/middleware.py`
- `src/petasos/mcp/outcomes.py`
- `src/petasos/serve.py`
- `src/petasos/limits.py`
- `scripts/**`
- `docs/**`
- `demo/**`
- `site/**`
- `Dockerfile`
- `.dockerignore`
- `fly.toml`

Every existing test file is outside `wall_expected` and so outside the wall; 003's tests must
pass unmodified (spec test 16). If a 001, 002, or 003 interface does not do what this spec
needs, stop and flag; do not work around it by editing those packages. The 001 review's
should-fix items are not this change's.

The merge gate enforces this list mechanically from the copy of this file on `main`.

## Parallel-safety note

This change and 005 (deploy) both depend only on 003 and build in parallel. Their walls are
disjoint: 005 owns `Dockerfile`, `.dockerignore`, `fly.toml`, `docs/**`, `scripts/**`,
`src/petasos/serve.py`, `src/petasos/limits.py`, `src/petasos/sessions/maintenance.py`, and its
own tests; it never edits `app.py`, and it composes the process around
`create_app(db, clock=clock)`, whose signature this change keeps (spec 4.10). Under the strict
up-to-date rule, whichever of the two merges second gets one "behind main" repair from the
builder; that repair is a merge of `main`, not new work, because no file is shared.

Change 007 (the demo app) depends on this change and builds after it merges; its fixtures
must match the response key sets this change's test 17 records.

## Build order

1. `mcp/service.py`: move `TOOL_ARG_SPEC`, `ALLOWED_TOOLS`, the raw shape check (as
   `shape_ok`), the card builder (as `approval_card`), and `approve_and_run` out of
   `mcp/tools.py` and `mcp/server.py`; add `PROPOSABLE_TOOLS`; re-export the old names from
   their old modules with `__all__` (`_shape_ok` included). Run the whole 003 suite: it must
   pass unmodified (spec test 16). Then `tests/test_mcp_service.py` (the service halves of
   spec tests 10 and 16).
2. `owner/outcomes.py` (spec 4.6), `owner/auth.py` (spec 4.1), `owner/origins.py` (spec 4.3),
   `owner/reads.py` (`ticket_activity`, `ticket_meta`), `owner/cards.py` (`owner_card`, spec
   4.7), `owner/ledger_view.py` (spec 4.9). Unit tests for the origin gate against a recording
   ASGI app (spec test 3, the gate half), the ledger view against a seeded database (spec test
   12, the view half), and the sentences (spec test 14, the sentences half).
3. `owner/api.py` (spec 4.4, 4.5) and `app.py` (spec 4.10), plus the one-line label in
   `guard_seam.py`. Spec tests 1, 2, 3 (full app), 4 to 9, 11, 12, 13, 14, 15, then the parity
   test 10 through the in-process MCP client, then 17 and the reader test 18.
4. `uv run ruff check .`, `uv run ruff format --check .`, `uv pip check`, `uv run pytest`;
   freeze a commit; review the diff against every acceptance test; run the wall check; write
   `report.md`; open the pull request.

Every test database lives under pytest's `tmp_path`; every timestamp comes from the
`frozen_clock` fixture; HTTP tests use Starlette's `TestClient` (a generator body is how a test
sends a chunked request) and the pinned `mcp` client in process; seam tests record every
downstream ASGI `send` message. No test opens the network or a real clock.

## Facts the planning thread verified (2026-09-30, pinned starlette 1.7.0, fastapi 0.141.1)

- A route handler's `request.state.identity = x` writes into the `scope["state"]` dict an
  outer raw ASGI layer holds, and 003's seam ledgers that identity on a hit (spec 4.1).
- A raw ASGI layer outside `GuardSeam` sees the seam's replaced 409 start message and can add
  headers to it (spec 4.3).
- `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` leaves only the app's own
  routes; plain `Route`s with a path parameter appended to `app.router.routes` work.
- `TestClient` sends a generator body as chunked with no `Content-Length`, and a bodiless
  `POST` with `Content-Length: 0`.
- Adding a `browser_origins` parameter to `create_app` fails 003's
  `test_create_app_signature_has_no_tokens_parameter`; that is why origins are a patched
  module constant.
- Importing `petasos.owner` from `petasos.mcp.tools` is a circular import; the dependency
  runs one way only.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if moving the shared logic into `mcp/service.py` breaks any
003 test, flag it with the failing test name rather than editing that test. If the pinned
Starlette cannot run a plain `def` route in its thread pool the way spec 4.5 relies on, flag
it rather than switching the handlers to `async def`. If any test needs a real clock, a sleep,
or the network, flag it.
