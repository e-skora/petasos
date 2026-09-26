# CLAUDE.md

Petasos is an open-source trust gate for AI agents (a Python library, a reference MCP server with a fake help desk, and a demo app). The rules for working here are in **AGENTS.md**. Read it first, then PRODUCT.md, ARCHITECTURE.md, DECISIONS.md, and the change folder you were assigned under `changes/`.

Claude-specific notes:

- When run by the GitHub Action on a schedule, your job is `changes/`: find the lowest-numbered change whose `proposal.md` says `status: ratified` and has no open PR titled `[build] NNN-slug`, claim it with an empty commit on `build/NNN-slug`, and follow AGENTS.md §2. If nothing is eligible, exit without changes.
- When mentioned with `@claude` on an issue or PR, do exactly what the comment asks within that PR's wall; do not start a new change.
- Never edit this file, AGENTS.md, DECISIONS.md, PRODUCT.md, ARCHITECTURE.md, DESIGN.md, any `proposal.md` or `spec.md`, the workflows, or `tests/test_no_private_identifiers.py`. Those are Elias's and the Cowork planning thread's.
- Tests: `uv run pytest`. Lint: `uv run ruff check . && uv run ruff format --check .`. UI: `cd demo && npm ci && npm test`.
- CI is the merge gate. Report your local count as advisory and name the commit it was measured at.
- Verify on disk before claiming anything: git HEAD, file contents, test output. Never restate a prior chat's claim as fact.
- Prose: plain language, define terms on first use, no em dashes.
