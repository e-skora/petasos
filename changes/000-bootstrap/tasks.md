# Tasks: 000-bootstrap

Each task names the check that proves it done. Tick `- [ ]` to `- [x]` in the same commit that
completes it.

- [ ] Create `src/petasos/__init__.py` with `__version__ = "0.1.0"`.
      Check: `uv run python -c "import petasos; print(petasos.__version__)"` prints `0.1.0`.
- [ ] Write `tests/test_smoke.py` asserting `petasos.__version__` is a non-empty string.
      Check: `uv run pytest tests/test_smoke.py -q` passes.
- [ ] Run `uv sync` and commit the resulting `uv.lock`.
      Check: `uv run pip check` reports no broken requirements.
- [ ] Write `Dockerfile` that installs the project with `uv` and runs uvicorn against
      `petasos.app:create_app --factory`.
      Check: `docker build .` succeeds (advisory only; not run in CI for this change, since
      `petasos.app` does not exist until change 003).
- [ ] Write `fly.toml` for app `petasos-api`, `auto_stop_machines = false`, an HTTP health
      check against `/healthz`.
      Check: `python3 -c "import tomllib; tomllib.load(open('fly.toml','rb'))"` parses
      without error, and the file contains `auto_stop_machines = false` and a `/healthz` path.
- [ ] Write `docs/setup.md` listing, in order: create the GitHub repository; run
      `/install-github-app`; run `claude setup-token` and add `CLAUDE_CODE_OAUTH_TOKEN`; add
      `FLY_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and
      `PRIVATE_DENYLIST` as repository secrets; connect the Codex GitHub integration and
      enable automatic reviews; set branch protection on `main` (required checks: python,
      demo, private-identifiers; one required approving review; linear history); create the
      Cloudflare zone for `petasos.io`; point the `api` CNAME at the Fly app; create the
      Cloudflare Pages project.
      Check: a fresh reader with no other context can follow the list top to bottom with no
      missing step.
- [ ] Confirm the full CI workflow passes on the claim branch: `python` (sync, ruff check,
      ruff format check, pip check, pytest), `demo` (skips cleanly, no `demo/package.json`
      yet), `private-identifiers` (skips locally, wired for the CI secret).
      Check: the GitHub Actions run for the PR shows all three jobs green (or gracefully
      skipped for `demo`).
- [ ] Write `changes/000-bootstrap/report.md` per AGENTS.md section 5, recording the exact
      dependency versions resolved and the `pip check` result.
      Check: `grep -nE '<!--|TODO|TBD|\[fill' changes/000-bootstrap/report.md` returns nothing.
