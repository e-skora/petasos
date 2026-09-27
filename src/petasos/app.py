"""The composition root: builds the FastAPI app, mounts `/healthz` and `/mcp`.

Change 001 onward adds the trust gate, memory, and the help-desk tools; this change
proves the shape (identity middleware, MCP session lifespan, exact routing) that they
build on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from petasos.mcp.middleware import IdentityMiddleware
from petasos.mcp.server import (
    build_mcp_server,
    mcp_routes,
    mcp_session_manager,
    run_session_manager,
)


def create_app(tokens: Mapping[str, str]) -> FastAPI:
    """Build the app. `tokens` maps a client id to its bearer token; nothing here
    reads an environment variable."""
    mcp_server = build_mcp_server()
    session_manager = mcp_session_manager(mcp_server)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with run_session_manager(session_manager):
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(IdentityMiddleware, tokens=tokens)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    app.router.routes.extend(mcp_routes(session_manager))

    return app
