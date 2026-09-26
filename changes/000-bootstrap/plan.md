# Plan: 000-bootstrap

grounded_at: `110b9a0` (the seed commit on `main`, 2026-09-26; the planning thread sets this
value at ratification, and the builder only checks it)

lane: claude

## Wall

wall_expected:
- `src/petasos/__init__.py`
- `tests/test_smoke.py`
- `docs/setup.md`
- `uv.lock`
- `fly.toml`
- `Dockerfile`
- `changes/000-bootstrap/tasks.md` (ticking boxes only)
- `changes/000-bootstrap/report.md`

wall_forbidden: everything listed as standing-forbidden in AGENTS.md section 1: `AGENTS.md`,
`CLAUDE.md`, `DECISIONS.md`, `PRODUCT.md`, `ARCHITECTURE.md`, `DESIGN.md`,
`changes/**/proposal.md`, `changes/**/spec.md`, `changes/QUESTIONS.md` (append-only
exception), `.github/workflows/**`, `tests/test_no_private_identifiers.py`. The workflows,
`pyproject.toml`, `.gitignore`, `LICENSE`, `README.md`, `.github/CODEOWNERS`, and
`.github/pull_request_template.md` are seeded directly into the repository's first commit
before the builder claims this change, so they are not part of the builder's wall in either
direction: do not edit them, and do not expect to need to.

## Build order

1. `src/petasos/__init__.py`: a package that sets `__version__ = "0.1.0"` and nothing else.
2. `tests/test_smoke.py`: imports `petasos`, asserts `petasos.__version__` is a non-empty
   string.
3. Run `uv sync` so `uv.lock` is generated and committed. From the next change onward CI
   installs with `uv sync --locked`, so the lockfile must be committed here.
4. `Dockerfile`: a small image that installs the project with `uv` and runs
   `uvicorn petasos.app:create_app --factory --host 0.0.0.0 --port 8080`. `petasos.app` does
   not exist yet; this file is accepted as inert scaffolding for change 005 and is not built
   or run in this change's CI.
5. `fly.toml`: app name `petasos-api`, `auto_stop_machines = false`, an HTTP health check
   against `/healthz`.
6. `docs/setup.md`: the one-time steps only Elias can do, in order: create the GitHub
   repository, run `/install-github-app` for the Claude GitHub App, run `claude setup-token`
   and add `CLAUDE_CODE_OAUTH_TOKEN`, add `FLY_API_TOKEN`, `CLOUDFLARE_API_TOKEN`,
   `CLOUDFLARE_ACCOUNT_ID`, and `PRIVATE_DENYLIST` as repository secrets, connect the Codex
   GitHub integration and enable automatic reviews, set branch protection on `main` (required
   checks: python, demo, private-identifiers; one required approving review; linear history),
   create the Cloudflare zone for `petasos.io`, point the `api` CNAME at the Fly app, and
   create the Cloudflare Pages project.

## Stop-and-flag conditions specific to this change

In addition to AGENTS.md section 3: if any dependency pin already recorded in `pyproject.toml`
cannot be resolved by `uv sync` (removed from PyPI, yanked since the pin was written, or
incompatible with Python 3.12), stop, do not silently swap in a different version, and flag
it with the exact error in `report.md`.
