"""Identity middleware: 401 before the MCP handshake (spec 0.7).

Full per-client identity, memory-tier ceilings, and action scopes are change 003's
`identity.py` (ARCHITECTURE.md section 3). This change only proves the fail-closed
shape: a request to `/mcp` needs a bearer token matching one of the configured
tokens, and carries no `Origin` header, or it gets a bare 401.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, Mapping

from starlette.types import ASGIApp, Receive, Scope, Send

_GUARDED_PATHS = frozenset({"/mcp", "/mcp/"})


class IdentityMiddleware:
    """Raw ASGI middleware, not `BaseHTTPMiddleware`, so it never buffers the
    streamable HTTP transport's response body."""

    def __init__(self, app: ASGIApp, tokens: Mapping[str, str]) -> None:
        self._app = app
        self._tokens = tuple(tokens.values())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in _GUARDED_PATHS:
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        if b"origin" in headers or not self._authorized(headers.get(b"authorization")):
            await _send_empty_401(send)
            return

        await self._app(scope, receive, send)

    def _authorized(self, header_value: bytes | None) -> bool:
        if header_value is None:
            return False
        scheme, _, token = header_value.decode("latin-1").partition(" ")
        if scheme != "Bearer" or not token:
            return False
        return any(hmac.compare_digest(token, candidate) for candidate in self._tokens)


async def _send_empty_401(send: Callable[[dict], Awaitable[None]]) -> None:
    await send({"type": "http.response.start", "status": 401, "headers": []})
    await send({"type": "http.response.body", "body": b""})
