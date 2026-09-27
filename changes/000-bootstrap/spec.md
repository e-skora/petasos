# Spec: 000-bootstrap

Binding once the proposal is ratified. The builder implements this table; a task that seems
to need a different answer is a stop-and-flag.

## 1. Decisions for this change

| # | Decision | Choice | Why |
|---|---|---|---|
| 0.1 | Environment manager | `uv`, with the `uv.lock` the planning thread committed; CI installs with `uv sync --locked` and selects Python 3.12 through setup-uv | Fast, single-file lock, matches DECISIONS.md D-003. |
| 0.2 | Lint and format | `ruff check .` and `ruff format --check .`, both clean in CI | One tool for both jobs, already pinned in `pyproject.toml`. |
| 0.3 | Test runner | `pytest`, `testpaths = ["tests"]`, `asyncio_mode = "auto"` | Matches AGENTS.md section 8's local commands. |
| 0.4 | Package layout | `src/petasos/`, installed in editable mode by `uv sync` | Keeps the import path clean and matches ARCHITECTURE.md section 3. |
| 0.5 | Dependency pins | Exact versions already recorded in `pyproject.toml` and `uv.lock` (both committed by the planning thread, both outside this change's wall); the report lists the installed versions | ARCHITECTURE.md section 8 requires exact pins and a `pip check` on every dependency change. |
| 0.6 | Smoke test | `tests/test_smoke.py` imports `petasos` and asserts `petasos.__version__` is a non-empty string | The lowest bar that still proves the whole loop (uv, pytest, CI) works end to end. |
| 0.7 | Authenticated MCP `ping` | `petasos.app.create_app(tokens: Mapping[str, str])` returns a FastAPI app with `GET /healthz` (unauthenticated, returns `{"ok": true}`) and an MCP server from the pinned `mcp` SDK, streamable HTTP transport, reached at `/mcp`, with one tool `ping` that returns the text `pong`. `tokens` maps a client id to its bearer token; the app reads no environment variable. A request to `/mcp` without `Authorization: Bearer <token>` matching some entry (constant-time compare), or with any `Origin` header, gets 401 with an empty body before the MCP layer sees it. The host app owns the MCP session manager's lifespan. `src/petasos/mcp/` holds the middleware and the server; change 003 extends them. | Review finding 14: prove the `mcp` 2.x server API, routing, lifespan, and auth before 003 depends on them. |
| 0.8 | Setup document | `docs/setup.md` records, in order, what only Elias can do and what the planning thread already did (see the task). Each vendor step cites the vendor doc URL it was checked against and the date. | A reader sees exactly which steps were human. |

## 2. Public interface

```python
import petasos

petasos.__version__  # "0.1.0"
```

## 3. Acceptance tests (plain language; each becomes a test named for it)

1. `uv sync --locked` succeeds, and `uv pip check` reports that all installed packages are
   compatible.
2. `uv run pytest` passes, including the smoke test.
3. `uv run ruff check .` and `uv run ruff format --check .` both exit clean.
4. The `ci.yml` workflow's `python` job runs, in this order on a fresh checkout: install uv
   with Python 3.12, `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv pip check`, `uv run pytest -q`. (The workflow already does this; the check is that it
   passes on this change's pull request.)
5. The `demo` job in `ci.yml` does not fail when `demo/package.json` does not exist yet: it
   skips its npm steps and still reports success.
6. The `private-identifiers` job runs `tests/test_no_private_identifiers.py`: locally it skips
   with a visible warning when no denylist is set; in CI it fails unless the
   `PRIVATE_DENYLIST` secret is set and the scan passes.
7. `fly.toml` parses as valid TOML, sets `auto_stop_machines = false`, and defines an HTTP
   health check against `/healthz`.
8. The `Dockerfile` builds an image whose entrypoint runs uvicorn against
   `petasos.app:create_app` (the Dockerfile is not built in CI for this change).
9. `GET /healthz` returns 200 without a token.
10. `POST /mcp` with no token, a wrong token, or a valid token plus an `Origin` header returns
    401 with an empty body, and the MCP layer is never called (the test counts handler calls).
11. With a valid token, an MCP client from the pinned SDK, talking to the app in-process over
    streamable HTTP, initializes, lists exactly one tool named `ping`, and calling it returns
    `pong`.
12. `/mcp` and `/mcp/` behave the same: neither answers with a redirect.
13. Entering and leaving the app's lifespan (the test client's context manager) starts and
    stops the MCP session manager with no warning or error logged.
