"""RequestSizeLimit: the request size bound, outermost in the deployed process
(spec 005 5.4, ARCHITECTURE.md section 7). A request that is too large, or whose
length is unknown because it is chunked, is refused before the inner app (and so
before the guard seam and the identity middleware) ever sees it.
"""

from __future__ import annotations

import json
import re

from starlette.types import ASGIApp, Receive, Scope, Send

MAX_REQUEST_BYTES = 65_536

_DIGITS_RE = re.compile(r"^[0-9]+$")

SENTENCES: dict[str, str] = {
    "refused/length_required": ("This demo needs to know how long a request is before reading it."),
    "refused/too_large": "That request is too large for this demo.",
}

_LENGTH_REQUIRED_PAYLOAD = {
    "status": "refused/length_required",
    "explanation": SENTENCES["refused/length_required"],
    "tier": None,
    "verb": None,
    "grant_id": None,
}
_TOO_LARGE_PAYLOAD = {
    "status": "refused/too_large",
    "explanation": SENTENCES["refused/too_large"],
    "tier": None,
    "verb": None,
    "grant_id": None,
}
_LENGTH_REQUIRED_BODY = json.dumps(_LENGTH_REQUIRED_PAYLOAD, separators=(",", ":")).encode("utf-8")
_TOO_LARGE_BODY = json.dumps(_TOO_LARGE_PAYLOAD, separators=(",", ":")).encode("utf-8")


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


class RequestSizeLimit:
    """Raw ASGI middleware, attribute `app`. Checks, in order: any
    `transfer-encoding` header at all (411, since a request carrying both that and
    `content-length` would otherwise read an unbounded chunked body); a
    `content-length` that is not a string of ASCII digits or that exceeds
    `max_bytes` (413). Everything else, including a request with neither header,
    passes through untouched."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        if _header(headers, b"transfer-encoding") is not None:
            await self._refuse(send, 411, _LENGTH_REQUIRED_BODY)
            return

        content_length = _header(headers, b"content-length")
        if content_length is not None:
            raw = content_length.decode("latin-1")
            if not _DIGITS_RE.match(raw) or int(raw) > self._max_bytes:
                await self._refuse(send, 413, _TOO_LARGE_BODY)
                return

        await self.app(scope, receive, send)

    async def _refuse(self, send: Send, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
