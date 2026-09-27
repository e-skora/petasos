# Report: 000-bootstrap

Verdict: BUILT
Summary: A reader can now clone the repository, run `uv sync --locked && uv run pytest`,
and see the smoke test and a live, authenticated MCP server pass. The server exposes
`GET /healthz` (no token) and `POST /mcp` (bearer token required, any `Origin` header
refused, streamable HTTP transport, one tool named `ping`), proving the `mcp` 2.x
server API, exact-route mounting, and the host-owned session manager lifespan that
change 003 will extend with the help-desk tools. `docs/setup.md` records the one-time
steps Elias still has to do by hand, and `Dockerfile` and `fly.toml` are ready for
change 005 to wire up.
Commit: this report is committed on branch build/000-bootstrap; see the pull request's
head commit for the exact sha (a report cannot correctly name the hash of the commit
that contains it).
Tests: CI run https://github.com/e-skora/petasos/actions/runs/36314920716 (`python`,
`demo`, `private-identifiers` all succeeded) on pull request #10's opening commit;
its head will move once this commit lands, and the merge gate reads whichever run CI
posts for that head commit (D-013: CI is the merge gate, never this report). Local
count 176 passed / 0 failed, 1 skipped (the private-identifier gate, which only runs
where the `PRIVATE_DENYLIST` secret is set), run against this same tree (advisory).
Changed files:
- Dockerfile
- changes/000-bootstrap/tasks.md
- docs/setup.md
- fly.toml
- src/petasos/__init__.py
- src/petasos/app.py
- src/petasos/mcp/__init__.py
- src/petasos/mcp/middleware.py
- src/petasos/mcp/server.py
- tests/test_app_mcp.py
- tests/test_smoke.py
- changes/000-bootstrap/report.md (this file)
Deviations from spec: This build had no live network access, so the vendor doc URLs
in `docs/setup.md` (Claude Code, Fly.io, Cloudflare) are the builder's best knowledge
as of 2026-09-27 rather than freshly fetched and confirmed working. `docs/setup.md`
says so plainly at the top, so Elias knows to double-check a link before following it
rather than trusting it blindly. The MCP client-connect commands in part 3 are marked
"not yet tested with a live client", as the plan asked.
Dependencies changed: none; `uv sync --locked` installed the pins already recorded in
`pyproject.toml` and `uv.lock` without modifying either. `uv pip check`: all 44
installed packages are compatible. Installed versions of the packages named in
ARCHITECTURE.md section 8: fastapi 0.141.1, uvicorn 0.54.0, mcp 2.2.0 (with its
mcp-types 2.2.0 and its httpx2 2.13.1 dependency for the client transport used only in
tests), pydantic 2.13.5, aiosqlite 0.22.1, httpx 0.28.1, pytest 9.1.1,
pytest-asyncio 1.4.0, hypothesis 6.168.1, ruff 0.16.9, starlette 1.7.0 (a transitive
pin of `mcp`).
Flags: none
Review findings and disposition: none yet; this section is filled in after review.
Open questions appended to QUESTIONS.md: none
