"""The MCP server: tool definitions and the streamable HTTP wiring.

Change 003 adds the help-desk tools here; this change proves the `mcp` 2.x server API,
routing, and lifespan with one tool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

MCP_PATH = "/mcp"


def build_mcp_server() -> MCPServer:
    """Build the MCPServer with its tools registered.

    A separate function (rather than a module-level singleton) so tests can build a
    fresh server, with its own session manager, per app instance.
    """
    server = MCPServer("petasos")

    @server.tool()
    def ping() -> str:
        return "pong"

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
