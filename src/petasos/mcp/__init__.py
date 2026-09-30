"""petasos.mcp: the MCP server over streamable HTTP, the identity middleware, the
session-id bindings, and the guard seam every response passes through exactly once.
"""

from __future__ import annotations

from petasos.mcp.guard_seam import UNGUARDED_ROUTES, GuardSeam
from petasos.mcp.identity import ClientRegistry, Identity
from petasos.mcp.middleware import IdentityMiddleware
from petasos.mcp.server import build_mcp_server
from petasos.mcp.tools import GateFactory, tool_result

__all__ = [
    "UNGUARDED_ROUTES",
    "ClientRegistry",
    "GateFactory",
    "GuardSeam",
    "Identity",
    "IdentityMiddleware",
    "build_mcp_server",
    "tool_result",
]
