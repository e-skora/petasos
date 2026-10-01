# Tasks: 004-owner-api

Each task names the acceptance test numbers from `spec.md` section 3 it satisfies. Tick
`- [ ]` to `- [x]` in the same commit that completes it.

- [x] Write `mcp/service.py`: move `TOOL_ARG_SPEC`, `ALLOWED_TOOLS`, the raw shape check
      (`shape_ok`), the card builder (`approval_card`), and `approve_and_run` out of
      `mcp/tools.py` and `mcp/server.py`; add `PROPOSABLE_TOOLS`; re-export the old names
      (including `_shape_ok`) from their old modules with `__all__` (spec 4.7, 4.8). Run the
      whole 003 suite unmodified. Write `tests/test_mcp_service.py`. Satisfies the service
      halves of acceptance tests 10 and 16.
- [x] Write `owner/outcomes.py`, `owner/auth.py` (`identity_for`, `request.state.identity`),
      `owner/origins.py` (`OriginGate`, `BROWSER_ORIGINS`), `owner/reads.py`
      (`ticket_activity`, `ticket_meta`), `owner/cards.py` (`owner_card`), and
      `owner/ledger_view.py` (`rows_for` and the description table) (spec 4.1, 4.3, 4.6, 4.7,
      4.9). Satisfies the gate half of test 3, the view half of test 12, and the sentences half
      of test 14.
- [x] Write `owner/api.py` (`owner_routes`, the check order, bounds, quotas, the ten endpoints
      of spec 4.5, plain `def` handlers), extend `app.py` (`OriginGate` outermost reading
      `origins.BROWSER_ORIGINS`, the routes, `redirect_slashes=False`, docs URLs off, signature
      unchanged), add the `owner` label in `guard_seam.py`, and the package `__init__` exports
      (spec 4.4, 4.5, 4.10, 4.11). Satisfies acceptance tests 1 to 9, 11 to 15, 17, the MCP half
      of 10, and the reader test 18.
- [ ] Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
      `uv pip check`, `uv run pytest` (the 003 suite unmodified is acceptance test 16); freeze
      a commit; review the diff against every acceptance test; run the wall check; write
      `report.md`; open the pull request.
