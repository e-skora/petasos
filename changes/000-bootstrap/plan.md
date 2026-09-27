# Plan: 000-bootstrap

grounded_at: `adad3c9` (the `main` commit that merged planning PR #3, 2026-09-26; the planning
thread set this value at ratification, and the builder only checks it)

lane: claude

## Wall

wall_expected:
- `src/petasos/__init__.py`
- `src/petasos/app.py`
- `src/petasos/mcp/**`
- `tests/test_smoke.py`
- `tests/test_app_*.py`
- `docs/setup.md`
- `fly.toml`
- `Dockerfile`
- `changes/000-bootstrap/tasks.md` (ticking boxes only)
- `changes/000-bootstrap/report.md`

wall_forbidden: everything standing-forbidden in AGENTS.md section 1, which includes
`.github/**` and every `plan.md`. `pyproject.toml` is outside the wall: its pins are already
recorded, and needing a new dependency is a stop-and-flag. The workflows, the merge gate, `.gitignore`, `LICENSE`, `README.md`,
`.github/CODEOWNERS`, and `.github/pull_request_template.md` are already on `main`; do not edit
them and do not expect to need to.

The merge gate enforces this list mechanically from the copy of this file on `main`.

## Build order

1. `src/petasos/__init__.py`: a package that sets `__version__ = "0.1.0"` and nothing else.
2. `tests/test_smoke.py`: imports `petasos`, asserts `petasos.__version__` is a non-empty
   string.
3. Run `uv sync --locked` against the committed `uv.lock`. Do not regenerate or edit the
   lockfile; if it does not install, stop and flag with the exact error.
4. `src/petasos/app.py` and `src/petasos/mcp/`: `create_app(tokens)` with `/healthz`, the
   identity middleware, and the MCP server with the one `ping` tool (spec 0.7). Read the
   installed `mcp` package's own documentation and source for the 2.x server class, the
   streamable HTTP app, and the session manager's lifespan; the 2.x migration guide
   (https://py.sdk.modelcontextprotocol.io/migration/) says the server class was renamed and
   transport settings moved. Do not write against 1.x examples from memory.
   Tests `tests/test_app_*.py` cover acceptance tests 9 to 13.
5. `Dockerfile`: a small image that installs the project with `uv` and runs
   `uvicorn petasos.app:create_app --factory --host 0.0.0.0 --port 8080`. Change 005 decides
   where the tokens come from at deploy time; until then the factory call is not run in a
   container.
6. `fly.toml`: app name `petasos-api`, `auto_stop_machines = false`, an HTTP health check
   against `/healthz`.
7. `docs/setup.md`, in two parts.
   Part 1, already done by the planning thread (record it, do not redo it): the GitHub
   repository; the `main` ruleset (required checks `python`, `demo`, `private-identifiers`;
   zero required reviews; linear history; no force push; no deletion); the merge gate
   (`.github/workflows/merge-gate.yml`), with one paragraph on how a build merges.
   Part 2, the one-time steps only Elias can do, in order: install the Claude GitHub App
   (`/install-github-app` in Claude Code); run `claude setup-token` and add
   `CLAUDE_CODE_OAUTH_TOKEN`; add `PRIVATE_DENYLIST` (unless already set); connect the Codex
   GitHub integration and turn on automatic reviews; later, for change 005, add
   `FLY_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, create the Cloudflare
   zone for `petasos.io`, point the `api` CNAME at the Fly app, and create the Pages project.
   Each step says where it is done (a terminal, Claude Code, github.com, chatgpt.com) and
   cites the vendor doc it was checked against.
   Part 3, connecting an MCP client to a local server: the command for Claude Code and for
   Codex, each copied from the vendor doc it cites, marked "not yet tested with a live
   client" until someone has.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if any dependency pin already recorded in `pyproject.toml`
cannot be installed by `uv sync --locked` (removed from PyPI, yanked since the pin was written, or
incompatible with Python 3.12), stop, do not silently swap in a different version, and flag
it with the exact error in `report.md`.
