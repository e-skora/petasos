"""Identity middleware: 401 before the MCP handshake, and the session-id-to-identity
bindings that keep a transport's `Mcp-Session-Id` from ever becoming a credential
(spec 3.3).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from petasos.mcp.identity import ClientRegistry, Identity

_GUARDED_PATHS = frozenset({"/mcp", "/mcp/"})


class IdentityMiddleware:
    """Raw ASGI middleware, not `BaseHTTPMiddleware`, so it never buffers the
    streamable HTTP transport's response body."""

    def __init__(
        self,
        app: ASGIApp,
        registry: ClientRegistry,
        clock: Callable[[], datetime],
        *,
        max_bindings: int = 10_000,
    ) -> None:
        self._app = app
        self._registry = registry
        self._clock = clock
        self._max_bindings = max_bindings
        self._bindings: dict[str, tuple[str, datetime]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in _GUARDED_PATHS:
            await self._app(scope, receive, send)
            return

        now = self._clock()
        self._prune_expired(now)

        headers = dict(scope["headers"])
        if b"origin" in headers:
            await _send_empty_401(send)
            return

        identity = self._authenticate(headers, now)
        if identity is None:
            await _send_empty_401(send)
            return

        session_id = _header(headers, b"mcp-session-id")
        if session_id is not None:
            binding = self._bindings.get(session_id)
            if binding is None or binding[0] != identity.id:
                await _send_empty_401(send)
                return
            if scope["method"] == "DELETE":
                del self._bindings[session_id]

        state = scope.setdefault("state", {})
        state["identity"] = identity

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                new_session_id = _header(dict(message.get("headers") or []), b"mcp-session-id")
                if new_session_id is not None:
                    self._bind(new_session_id, identity.id, identity.expires_at)
            await send(message)

        await self._app(scope, receive, wrapped_send)

    def _authenticate(self, headers: Mapping[bytes, bytes], now: datetime) -> Identity | None:
        header_value = headers.get(b"authorization")
        if header_value is None:
            return None
        scheme, _, token = header_value.decode("latin-1").partition(" ")
        if scheme != "Bearer" or not token:
            return None
        return self._registry.authenticate(token, now=now)

    def _bind(self, session_id: str, identity_id: str, identity_expires_at: datetime) -> None:
        if session_id not in self._bindings and len(self._bindings) >= self._max_bindings:
            oldest = next(iter(self._bindings))
            del self._bindings[oldest]
        self._bindings[session_id] = (identity_id, identity_expires_at)

    def _prune_expired(self, now: datetime) -> None:
        expired = [sid for sid, (_, expires_at) in self._bindings.items() if expires_at <= now]
        for sid in expired:
            del self._bindings[sid]


def _header(headers: Mapping[bytes, bytes], name: bytes) -> str | None:
    value = headers.get(name)
    return value.decode("latin-1") if value is not None else None


async def _send_empty_401(send: Callable[[Message], Awaitable[None]]) -> None:
    await send({"type": "http.response.start", "status": 401, "headers": []})
    await send({"type": "http.response.body", "body": b""})
