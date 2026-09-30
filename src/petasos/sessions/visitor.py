"""`POST /session`: mint a visitor session, or refuse over a quota or a ceiling with
nothing written (spec 3.19)."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from petasos.sessions.store import MintCeilingExceeded, MintQuotaExceeded, SessionStore

_SIMULATION_NOTE = (
    "The owner token is a simulation of approval for this demo, not proof that a person approved."
)


def session_routes(store: SessionStore) -> list[Route]:
    async def mint_session(request: Request) -> JSONResponse:
        try:
            minted = store.mint()
        except MintQuotaExceeded:
            return JSONResponse(
                {
                    "status": "refused/quota",
                    "explanation": (
                        "This demo has minted its hourly or daily allowance of sessions; "
                        "try again later."
                    ),
                },
                status_code=429,
            )
        except MintCeilingExceeded:
            return JSONResponse(
                {
                    "status": "refused/ceiling",
                    "explanation": "The demo is at capacity; try again later.",
                },
                status_code=503,
            )

        return JSONResponse(
            {
                "session": minted.session,
                "expires_at": minted.expires_at.isoformat(),
                "mcp_url": "/mcp",
                "tokens": minted.tokens,
                "note": _SIMULATION_NOTE,
            },
            status_code=201,
        )

    return [Route("/session", endpoint=mint_session, methods=["POST"])]
