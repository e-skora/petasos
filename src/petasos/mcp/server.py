"""The MCP server: tool registration, per-identity tool visibility, the raw
argument shape check, the per-session call quota, and the streamable HTTP wiring
(spec 3.4 to 3.8)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from petasos.mcp.service import ALLOWED_TOOLS, TOOL_ARG_SPEC
from petasos.mcp.service import shape_ok as _shape_ok
from petasos.mcp.tools import mcp_refusal, register_tools
from petasos.sessions import quotas

if TYPE_CHECKING:
    from petasos.storage import Database

MCP_PATH = "/mcp"


def _identity_or_none(ctx: ServerRequestContext) -> Any:
    request = ctx.request
    if request is None:
        return None
    return getattr(request.state, "identity", None)


class ToolAccessMiddleware:
    """Per-request context-tier middleware: filters `tools/list`, and refuses a
    `tools/call` for an unknown shape, a forbidden tool, or an exhausted quota,
    before any adapter runs (spec 3.4, 3.15, 3.21)."""

    def __init__(self, db: Database, clock: Callable[[], datetime]) -> None:
        self._db = db
        self._clock = clock

    async def __call__(self, ctx: ServerRequestContext, call_next):
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            identity = _identity_or_none(ctx)
            allowed = (
                ALLOWED_TOOLS.get(identity.role, frozenset())
                if identity is not None
                else frozenset()
            )
            tools = result.get("tools", []) if isinstance(result, Mapping) else []
            return {**result, "tools": [tool for tool in tools if tool.get("name") in allowed]}

        if ctx.method == "tools/call":
            params = ctx.params
            if not isinstance(params, Mapping):
                return mcp_refusal("refused/invalid_arguments")
            name = params.get("name")
            arguments = params.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, Mapping):
                return mcp_refusal("refused/invalid_arguments")

            identity = _identity_or_none(ctx)
            allowed = (
                ALLOWED_TOOLS.get(identity.role, frozenset())
                if identity is not None
                else frozenset()
            )
            if name not in allowed:
                return mcp_refusal("refused/not_allowed")

            if not _shape_ok(name, arguments):
                return mcp_refusal("refused/invalid_arguments")

            now = self._clock()
            with self._db.transaction() as conn:
                admitted = quotas.reserve(
                    conn,
                    scope=identity.scope,
                    family="calls",
                    limit=quotas.CALLS_PER_HOUR,
                    window=timedelta(hours=1),
                    now=now,
                )
                if admitted:
                    quotas.increment_counter(conn, "tool_calls")
            if not admitted:
                return mcp_refusal("refused/quota")

        return await call_next(ctx)


def build_mcp_server(*, db: Database, clock: Callable[[], datetime]) -> MCPServer:
    """Build the MCPServer with every help-desk and owner tool registered, and the
    `ToolAccessMiddleware` installed. A separate function (rather than a module-level
    singleton) so tests can build a fresh server, with its own session manager, per
    app instance."""
    server = MCPServer("petasos", middleware=[ToolAccessMiddleware(db, clock)])

    @server.tool()
    def ping() -> str:
        return "pong"

    register_tools(server, db=db, clock=clock)

    return server


def mcp_session_manager(server: MCPServer) -> StreamableHTTPSessionManager:
    """The server's session manager, for the host app to own the lifespan of.

    `MCPServer.session_manager` only exists after `streamable_http_app()` has run once
    (it is built lazily); that call's own Starlette app is discarded; only the session
    manager it built is kept. DNS-rebinding protection is disabled here because the
    identity middleware already rejects any request carrying an `Origin` header, and
    the manager's default host-based auto-enable assumes a local, single-host
    deployment that does not describe this app.
    """
    server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server.session_manager


def mcp_routes(session_manager: StreamableHTTPSessionManager) -> list[Route]:
    """Exact routes for `/mcp` and `/mcp/`, so neither redirects to the other.

    Built as plain ASGI (a `StreamableHTTPASGIApp` instance, not a function or a
    method), so Starlette treats the endpoint as an ASGI app rather than wrapping it
    as `func(request) -> response`, which would break the streaming transport.
    """
    asgi_app = StreamableHTTPASGIApp(session_manager)
    return [
        Route(MCP_PATH, endpoint=asgi_app),
        Route(f"{MCP_PATH}/", endpoint=asgi_app),
    ]


@asynccontextmanager
async def run_session_manager(session_manager: StreamableHTTPSessionManager) -> AsyncIterator[None]:
    """The host app's lifespan wraps this, so the session manager's lifetime matches the
    app's rather than a mounted sub-app's, whose lifespan Starlette never runs."""
    async with session_manager.run():
        yield


__all__ = [
    "ALLOWED_TOOLS",
    "MCP_PATH",
    "TOOL_ARG_SPEC",
    "ToolAccessMiddleware",
    "_shape_ok",
    "build_mcp_server",
    "mcp_routes",
    "mcp_session_manager",
    "run_session_manager",
]
