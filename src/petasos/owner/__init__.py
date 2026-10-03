"""The owner API: the second front door to the same `Gate`, `GrantStore`,
`Executor`, and `Ledger` the MCP tools use, served under `/owner/` for the demo app
(spec 004)."""

from __future__ import annotations

from petasos.owner.api import MAX_OWNER_BODY_BYTES, owner_routes
from petasos.owner.origins import OriginGate

__all__ = ["MAX_OWNER_BODY_BYTES", "OriginGate", "owner_routes"]
