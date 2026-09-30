# Tasks: 005-deploy-fly-cloudflare

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [ ] Write `limits.py` (`RequestSizeLimit`, `MAX_REQUEST_BYTES`, the 411 and 413 constant
      refusals, the header rules of spec 5.4). Satisfies acceptance test 3.
- [ ] Write `tests/support_005.py` (lifespan harness, tick gate, recording app) and
      `sessions/maintenance.py`: constants, `prune_archives`, `rotate_ledger` (batched
      archive first, read back, delete, append the `rotated` row, no `ATTACH`, archive file
      removed on failure, verify after commit), `reset_demo` (sessions and identities),
      `run_maintenance` (three named steps, backoff, the info line), `MaintenanceRunner`
      (injectable `sleep`, in-flight tick awaited on shutdown), and the command line (spec 5.5
      to 5.8). Satisfies acceptance tests 4 to 10.
- [ ] Write `serve.py` (`Settings`, `SettingsError`, `settings_from_environ`, `build_app`,
      `main`, `--check`, one worker, keep-alive 65) (spec 5.1, 5.13). Satisfies acceptance
      tests 1, 2, 14.
- [ ] Write `scripts/smoke_journey.py` (importable `run_journey` over `httpx2`, the steps and
      exit codes of spec 5.10, no token on any output path) and `tests/test_smoke_journey.py`.
      Satisfies acceptance test 11.
- [ ] Write `Dockerfile` (README.md in the first `COPY`, `uv` 0.12.21, `PATH` to the venv,
      `python -m petasos.serve`), `.dockerignore`, `fly.toml` exactly as spec 5.3,
      `docs/deploy.md`, and the `docs/setup.md` rewrite (spec 5.2, 5.3, 5.11, 5.12), plus
      `tests/test_deploy_files.py`. Satisfies acceptance tests 12, 13, and the reader test 16.
- [ ] Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
      `uv pip check`, `uv run pytest` (the 003 suite unmodified is acceptance test 15); freeze
      a commit; review the diff against every acceptance test; run the wall check; write
      `report.md`; open the pull request.
