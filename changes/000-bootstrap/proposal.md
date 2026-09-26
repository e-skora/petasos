# Proposal: 000-bootstrap

status: proposed
lane: claude
depends_on: none
decisions: D-003, D-005, D-006, D-007, D-012

## Why

Nothing else can build until the repository exists, has a working test loop, and the
autonomous build loop can actually run against it. This change turns the scaffolding files
already written (workflows, `pyproject.toml`, the license, the change-folder shape) into a
real repository with green CI, so change 001 has solid ground to build on.

## What this change delivers

1. The scaffolding already written landing on `main`: the GitHub Actions workflows, licence,
   `.gitignore`, `pyproject.toml`, `CODEOWNERS`, pull request template, and the change-folder
   shape.
2. A minimal `src/petasos/__init__.py` package that imports cleanly.
3. One smoke test (`tests/test_smoke.py`) proving the package imports and the test loop runs.
4. A `Dockerfile` that runs the API under uvicorn, and a `fly.toml` with `auto_stop_machines`
   set to false and a `/healthz` check, even though the API itself does not exist yet: both
   files are needed for change 005 and are safe to write now as inert scaffolding.
5. A minimal FastAPI app with `/healthz` and one authenticated MCP tool, `ping`, built on the
   pinned `mcp` 2.x SDK, so the server API, routing, lifespan, and the 401-before-handshake
   rule are proven before change 003 builds on them.
6. `docs/setup.md`, listing the exact one-time steps Elias performs by hand: creating the
   GitHub repository, installing the Claude GitHub App, minting the OAuth token, adding the
   secrets, connecting the Codex GitHub integration, and the Cloudflare and Fly one-time
   setup, plus what the planning thread already set up (the `main` ruleset and the merge
   gate).
7. CI green on all three jobs.

## What this change does not deliver

No trust logic, no memory, no help-desk tools (only `ping`), no demo app, no real deployment
run.

## Reader test

After this merges, a reader can clone the repository, run `uv sync && uv run pytest`, see the
smoke and `ping` tests pass, and follow `docs/setup.md` to understand exactly what Elias did by hand that
no agent could do for him.
