# Tasks: 000-bootstrap

Each task names the check that proves it done. Tick `- [ ]` to `- [x]` in the same commit that
completes it.

- [ ] Create `src/petasos/__init__.py` with `__version__ = "0.1.0"`.
      Check: `uv run python -c "import petasos; print(petasos.__version__)"` prints `0.1.0`.
- [ ] Write `tests/test_smoke.py` asserting `petasos.__version__` is a non-empty string.
      Check: `uv run pytest tests/test_smoke.py -q` passes.
- [ ] Run `uv sync` and commit the resulting `uv.lock`.
      Check: `uv run pip check` reports no broken requirements.
- [ ] Write `src/petasos/app.py` and `src/petasos/mcp/` per spec 0.7, with
      `tests/test_app_*.py` covering acceptance tests 9 to 13.
      Check: `uv run pytest tests -q -k app` passes.
- [ ] Write `Dockerfile` that installs the project with `uv` and runs uvicorn against
      `petasos.app:create_app --factory`.
      Check: the file exists and names that entrypoint (not built in CI for this change).
- [ ] Write `fly.toml` for app `petasos-api`, `auto_stop_machines = false`, an HTTP health
      check against `/healthz`.
      Check: `uv run python -c "import tomllib; tomllib.load(open('fly.toml','rb'))"` parses
      without error, and the file contains `auto_stop_machines = false` and a `/healthz` path.
- [ ] Write `docs/setup.md` in the three parts plan.md step 7 describes.
      Check: a fresh reader with no other context can follow part 2 top to bottom with no
      missing step, and every vendor step cites a URL.
- [ ] Confirm the full CI workflow passes on the claim branch: `python` (sync, ruff check,
      ruff format check, pip check, pytest), `demo` (skips cleanly, no `demo/package.json`
      yet), `private-identifiers` (skips locally, wired for the CI secret).
      Check: the GitHub Actions run for the PR shows all three jobs green (or gracefully
      skipped for `demo`).
- [ ] Write `changes/000-bootstrap/report.md` per AGENTS.md section 5, recording the exact
      dependency versions resolved and the `pip check` result.
      Check: `grep -nE '<!--|TODO|TBD|\[fill' changes/000-bootstrap/report.md` returns nothing.
