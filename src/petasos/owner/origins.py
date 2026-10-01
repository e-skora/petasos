"""The origin gate: allowed browser origins, preflight answers, and the
cross-origin response headers (spec 4.3). Hygiene for browsers, not the API's
protection: the bearer string is what protects every endpoint.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

BROWSER_ORIGINS = frozenset(
    {
        "https://petasos.io",
        "https://www.petasos.io",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }
)

_GATED_EXACT = frozenset({"/session"})
_GATED_PREFIX = "/owner/"

_REFUSAL_PAYLOAD = {
    "status": "refused/origin",
    "explanation": "This website is not allowed to use the demo's browser endpoints.",
    "tier": None,
    "verb": None,
    "grant_id": None,
}
_REFUSAL_BODY = json.dumps(_REFUSAL_PAYLOAD, separators=(",", ":")).encode("utf-8")


def _is_gated(path: str) -> bool:
    return path in _GATED_EXACT or path.startswith(_GATED_PREFIX)


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


class OriginGate:
    """Raw ASGI middleware, installed outermost. Wraps only `/session` and
    `/owner/*`; every other path (including `/mcp`) passes through untouched."""

    def __init__(self, app: ASGIApp, *, origins: frozenset[str]) -> None:
        self.app = app
        self._origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_gated(scope["path"]):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        origin = _header(headers, b"origin")
        if origin is None:
            await self.app(scope, receive, send)
            return

        origin_str = origin.decode("latin-1")
        if origin_str not in self._origins:
            await self._send_refusal(send)
            return

        request_method = _header(headers, b"access-control-request-method")
        if scope["method"] == "OPTIONS" and request_method is not None:
            await self._send_preflight(send, origin_str)
            return

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                new_headers = [*(message.get("headers") or [])]
                new_headers.append((b"access-control-allow-origin", origin_str.encode("latin-1")))
                new_headers.append((b"vary", b"Origin"))
                new_headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": new_headers}
            await send(message)

        await self.app(scope, receive, wrapped_send)

    async def _send_refusal(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"vary", b"Origin"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _REFUSAL_BODY, "more_body": False})

    async def _send_preflight(self, send: Send, origin: str) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", origin.encode("latin-1")),
                    (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                    (b"access-control-allow-headers", b"Authorization, Content-Type"),
                    (b"access-control-max-age", b"600"),
                    (b"vary", b"Origin"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})
