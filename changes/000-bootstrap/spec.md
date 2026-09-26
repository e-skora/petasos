# Spec: 000-bootstrap

Binding once the proposal is ratified. The builder implements this table; a task that seems
to need a different answer is a stop-and-flag.

## 1. Decisions for this change

| # | Decision | Choice | Why |
|---|---|---|---|
| 0.1 | Environment manager | `uv`, with a committed `uv.lock` | Fast, single-file lock, matches DECISIONS.md D-003. |
| 0.2 | Lint and format | `ruff check .` and `ruff format --check .`, both clean in CI | One tool for both jobs, already pinned in `pyproject.toml`. |
| 0.3 | Test runner | `pytest`, `testpaths = ["tests"]`, `asyncio_mode = "auto"` | Matches AGENTS.md section 8's local commands. |
| 0.4 | Package layout | `src/petasos/`, installed in editable mode by `uv sync` | Keeps the import path clean and matches ARCHITECTURE.md section 3. |
| 0.5 | Dependency pins | Exact versions already recorded in `pyproject.toml`, resolved against PyPI on 2026-09-26 and recorded in this change's `report.md` | ARCHITECTURE.md section 8 requires exact pins and a `pip check` on every dependency change. |
| 0.6 | Smoke test | `tests/test_smoke.py` imports `petasos` and asserts `petasos.__version__` is a non-empty string | The lowest bar that still proves the whole loop (uv, pytest, CI) works end to end. |

## 2. Public interface

```python
import petasos
petasos.__version__  # "0.1.0"
```

## 3. Acceptance tests (plain language; each becomes a test named for it)

1. `uv sync` succeeds with no network errors beyond fetching packages, and `uv run pip check`
   reports no broken requirements.
2. `uv run pytest` passes, including the smoke test.
3. `uv run ruff check .` and `uv run ruff format --check .` both exit clean.
4. The `ci.yml` workflow's `python` job runs all four of those steps, in that order, on a
   fresh checkout.
5. The `demo` job in `ci.yml` does not fail when `demo/package.json` does not exist yet: it
   skips its npm steps and still reports success.
6. The `private-identifiers` job runs `tests/test_no_private_identifiers.py` and passes
   locally as a skip (no secret set) and is wired to receive `PRIVATE_DENYLIST` from the
   repository secret in CI.
7. `fly.toml` parses as valid TOML, sets `auto_stop_machines = false`, and defines an HTTP
   health check against `/healthz`.
8. The `Dockerfile` builds an image whose entrypoint runs uvicorn against
   `petasos.app:create_app` (even though `create_app` does not exist until change 003; the
   Dockerfile is accepted as inert scaffolding for this change and is not run in CI).
