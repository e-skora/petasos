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
5. `docs/setup.md`, listing the exact one-time steps Elias performs by hand: creating the
   GitHub repository, installing the Claude GitHub App, minting the OAuth token, adding the
   five secrets, connecting the Codex GitHub integration, setting branch protection, and the
   Cloudflare and Fly one-time setup.
6. CI green on all three jobs (python, demo skipped gracefully, private-identifiers skipped
   locally and green in CI where the secret exists).

## What this change does not deliver

No trust logic, no memory, no MCP server, no demo app, no real deployment run. The
Dockerfile and fly.toml describe how the (future) app will run; they do not need a working
app to exist as files.

## Reader test

After this merges, a reader can clone the repository, run `uv sync && uv run pytest`, see the
smoke test pass, and follow `docs/setup.md` to understand exactly what Elias did by hand that
no agent could do for him.
