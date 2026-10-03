# Plan: 005-deploy-fly-cloudflare

grounded_at: `b9d872b` (the `main` commit that merged the 004-owner-api build, PR #27,
2026-10-02; the planning thread checked the merged 003 and 004 interfaces against this spec and
set this value at ratification under D-018; the builder only checks it)

lane: claude

## Wall

The merge gate reads one path per bullet, on one line.

wall_expected:
- `src/petasos/serve.py`
- `src/petasos/limits.py`
- `src/petasos/sessions/maintenance.py`
- `scripts/smoke_journey.py`
- `Dockerfile`
- `.dockerignore`
- `fly.toml`
- `docs/deploy.md`
- `docs/setup.md`
- `tests/support_005.py`
- `tests/test_serve_*.py`
- `tests/test_limits_*.py`
- `tests/test_maintenance_*.py`
- `tests/test_smoke_journey.py`
- `tests/test_deploy_files.py`
- `changes/005-deploy-fly-cloudflare/report.md`
- `changes/005-deploy-fly-cloudflare/tasks.md`

`sessions/maintenance.py` is a new module in 003's package; `sessions/__init__.py` is not
edited, and the module is imported by its full name. `scripts/` is a new folder. `tasks.md`:
ticking boxes only.

wall_forbidden: everything standing-forbidden in AGENTS.md section 1 (`.github/**` included:
the deploy workflow and its release gate `.github/scripts/release_gate.py`, with
`tests/test_release_gate.py`, were written by the planning thread in the same pull request as
this plan; the builder reads them and builds what the workflow calls, never edits them), plus:
- `src/petasos/app.py`
- `src/petasos/owner/**`
- `src/petasos/mcp/**`
- `demo/**`
- `site/**`
- `src/petasos/__init__.py`
- `src/petasos/trust/**`
- `src/petasos/ledger/**`
- `src/petasos/memory/**`
- `src/petasos/helpdesk/**`
- `src/petasos/storage.py`
- `src/petasos/sessions/store.py`
- `src/petasos/sessions/visitor.py`
- `src/petasos/sessions/quotas.py`
- `src/petasos/sessions/__init__.py`

Every existing test file is outside `wall_expected` and so outside the wall; 003's tests must
pass unmodified (spec test 15). If a 001 to 003 interface does not do what this spec needs,
stop and flag; do not work around it by editing those packages.

The merge gate enforces this list mechanically from the copy of this file on `main`.

## Pins and facts the planning thread verified on 2026-09-30 (the builder copies them)

Sources are the vendors' current pages, read on that date; `docs/deploy.md` cites this list
rather than inventing a date.

- `uv` image for the Dockerfile: `ghcr.io/astral-sh/uv:0.12.21` (release 0.12.21 of
  2026-09-29; the tag exists in the registry).
- `fly.toml` spellings, from the Fly configuration reference: `auto_stop_machines` takes the
  strings `"off"`, `"stop"`, `"suspend"`; `[mounts]` needs `source` and `destination`;
  `[[http_service.checks]]` keys are `grace_period`, `interval`, `method`, `timeout`, `path`,
  `protocol`; `[http_service.concurrency]` keys are `type`, `soft_limit`, `hard_limit`;
  `kill_timeout` is an integer number of seconds (default 5, maximum 300); `[[vm]]` keys are
  `size` and `memory`; the rolling deploy strategy is the default for apps with volumes, and
  `canary` and `bluegreen` are not allowed with volumes.
- `flyctl deploy` flags used by the workflow: `--app`, `--image-label`, `--remote-only`
  (the default), `--ha` (default true, so the workflow passes `--ha=false`).
- Fly with Cloudflare in front: `fly certs add`, `fly certs setup` and its `_fly-ownership`
  TXT record, proxied CNAME, SSL mode Full (strict), Always Use HTTPS; Fly states that
  DNS-01 (`_acme-challenge`) certificates conflict with Cloudflare Universal SSL and names a
  Cloudflare Origin Certificate as the fallback. Fly's volume pages do not describe mount
  ownership; they say one volume attaches to one machine, recommend two volumes per app,
  and describe daily snapshots kept 5 days by default.
- Cloudflare rate limiting on the Free plan: one rule, 10 second counting period and
  mitigation, counting by IP, rule fields Path and Verified Bot only, action Block; Bot Fight
  Mode may challenge API clients and cannot be skipped by a rule. The default upload limit
  is 100 MB; whether Free can lower it is not stated.
- GitHub `GET /repos/{owner}/{repo}/commits/{ref}/check-runs`: `check_runs[].name`,
  `status`, `conclusion`; `per_page` default 30, maximum 100; this repository's check names
  are the bare job ids `python`, `demo`, `private-identifiers`.
- Action pins for the workflow: `actions/checkout` v7.0.1 at
  `3d3c42e5aac5ba805825da76410c181273ba90b1`; `superfly/flyctl-actions/setup-flyctl` tag 1.6
  at `ed8efb33836e8b2096c7fd3ba1c8afe303ebbff1` with flyctl version `0.4.110`;
  `astral-sh/setup-uv` as `ci.yml` pins it.
- Library facts: `uvicorn.run(app_object, workers=1, timeout_graceful_shutdown=30,
  timeout_keep_alive=65, ...)` is accepted at uvicorn 0.54.0; an ASGI lifespan wrapper of the
  5.5 shape works with the MCP session manager under `TestClient` and real uvicorn, including
  SIGINT shutdown; cancelling `asyncio.to_thread` does not stop the thread; `chain.append`
  works inside `db.transaction()` after a full `DELETE`, ids continue from `sqlite_sequence`,
  the next `prev_hash` is the genesis hash, and `verify()` is `None`; no schema constraint or
  test enumerates ledger kinds; `TestClient` sends a bodiless `POST` with `content-length: 0`
  and a generator body as chunked; curl sends neither header for a bodiless `POST` and
  uvicorn passes it to the app; a request with both `content-length` and
  `transfer-encoding: chunked` reaches the app with an unbounded body unless refused; the
  pinned `mcp` client is typed against `httpx2.AsyncClient`, and the journey runs in process
  over `httpx2.ASGITransport`.

## Parallel-safety note

This change and 004 (owner API) both depend only on 003 and build in parallel. The walls are
disjoint: 004 owns `app.py`, `mcp/service.py`, `mcp/tools.py`, `mcp/server.py`,
`mcp/guard_seam.py`, `owner/**`; this change never edits `app.py` and calls
`create_app(db, clock=clock)` exactly as 003 defines it (004 keeps that signature). Under the
strict up-to-date rule, whichever of the two merges second gets one "behind main" repair from
the builder; that repair is a merge of `main`, not new work. Change 007 (the demo app) may
also build alongside this one; it touches only `demo/**`.

The deploy workflow is inert until Elias starts it by hand (D-015). Its first run needs the
one-time steps in `docs/deploy.md` (spec 5.11) done first; the workflow's CI gate and the
smoke journey fail closed if anything is missing.

## Build order

1. `limits.py` (spec 5.4) with its tests against a recording ASGI app with hand-built scopes
   and against the full app (spec test 3).
2. `tests/support_005.py` (the lifespan harness, the tick gate, the recording app), then
   `sessions/maintenance.py` (spec 5.5 to 5.8): constants, `prune_archives`, `rotate_ledger`
   (archive in batches, read back, then delete and append the `rotated` row, all under one
   main transaction and no `ATTACH`; delete the archive file on any failure), `reset_demo`,
   `run_maintenance`, `MaintenanceRunner` with the injectable `sleep`, and the command line.
   Test against a database seeded through `POST /session` and the frozen clock (spec tests 4
   to 10).
3. `serve.py` (spec 5.1): settings, `SettingsError`, `build_app`, `main`, `--check`. Spec
   tests 1, 2, 14.
4. `scripts/smoke_journey.py` (spec 5.10), importable, with `tests/test_smoke_journey.py`
   driving `run_journey` through `httpx2.ASGITransport` and the in-process MCP client, plus a
   stand-in `GET /owner/tickets/3` route built in the test for the receipt step (spec test 11).
5. `Dockerfile`, `.dockerignore`, `fly.toml` (spec 5.2, 5.3), `docs/deploy.md` and the
   `docs/setup.md` rewrite (spec 5.11, 5.12), and `tests/test_deploy_files.py` (spec tests 12,
   13). The reader test 16 last.
6. `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv pip check`,
   `uv run pytest` (the 003 suite unmodified is spec test 15); freeze a commit; review the
   diff against every acceptance test; run the wall check; write `report.md`; open the pull
   request.

Every test database lives under pytest's `tmp_path`; every timestamp comes from the
`frozen_clock` fixture; nothing sleeps, opens the network, builds an image, or touches Fly or
Cloudflare. The container image is first built by the deploy workflow's `flyctl deploy
--remote-only`; if it fails there, that is a planning-thread fix, not a builder repair.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if `Database.transaction()` cannot hold the write lock
across the batched archive copy (spec 5.7), flag it with the observed error rather than
splitting the rotation into two main transactions. If the lifespan harness cannot run the MCP
session manager's startup inside one task, flag it with the observed messages rather than
starting the tick from anywhere else. If any test needs a real sleep, a container runtime, or
a network call, flag it.
