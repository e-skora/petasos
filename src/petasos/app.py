"""The composition root: builds the FastAPI app, mounts `/healthz`, `POST /session`,
and `/mcp`, and wires the guard seam and identity middleware around all of it
(spec 3.23).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from petasos.mcp.guard_seam import GuardSeam
from petasos.mcp.identity import ClientRegistry
from petasos.mcp.middleware import IdentityMiddleware
from petasos.mcp.server import (
    build_mcp_server,
    mcp_routes,
    mcp_session_manager,
    run_session_manager,
)
from petasos.owner import OriginGate, owner_routes
from petasos.owner import origins as owner_origins
from petasos.sessions.store import SessionStore
from petasos.sessions.visitor import session_routes
from petasos.storage import Database


def create_app(db: Database, *, clock: Callable[[], datetime]) -> FastAPI:
    """Build the app. `db.migrate()` is the caller's job before this runs; nothing
    here reads an environment variable."""
    mcp_server = build_mcp_server(db=db, clock=clock)
    session_manager = mcp_session_manager(mcp_server)
    registry = ClientRegistry(db)
    session_store = SessionStore(db, clock=clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with run_session_manager(session_manager):
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False
    app.add_middleware(IdentityMiddleware, registry=registry, clock=clock)
    app.add_middleware(GuardSeam, db=db, clock=clock)
    app.add_middleware(OriginGate, origins=owner_origins.BROWSER_ORIGINS)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    app.router.routes.extend(session_routes(session_store))
    app.router.routes.extend(mcp_routes(session_manager))
    app.router.routes.extend(owner_routes(db, registry=registry, clock=clock))

    return app


if __name__ == "__main__":
    import uvicorn

    _db = Database(Path(os.environ["PETASOS_DB"]))
    _db.migrate()
    _app = create_app(_db, clock=lambda: datetime.now(UTC))
    uvicorn.run(_app, host="0.0.0.0", port=8080)
