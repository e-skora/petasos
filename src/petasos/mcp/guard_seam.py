"""The guard seam: the one ASGI layer every HTTP response passes through, releasing
a response only one complete, scanned unit at a time (spec 3.9, 3.10). Installed as
the outermost application middleware; only `UNGUARDED_ROUTES` skip it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from petasos.ledger.chain import append as ledger_append
from petasos.memory.canary import CanarySet, all_canaries, canary_set
from petasos.memory.guard import Hit
from petasos.memory.guard import scan as guard_scan
from petasos.sessions import quotas

if TYPE_CHECKING:
    from petasos.storage import Database

logger = logging.getLogger(__name__)

UNGUARDED_ROUTES = frozenset({"/healthz"})

MAX_BODY_BYTES = 1_048_576
MAX_EVENT_BYTES = 262_144

# A lone `\r` only counts as a line ending once at least one more byte has
# arrived after it: otherwise an incrementally-buffered `\r\n` would be
# misread as a already-terminated `\r` line the instant the `\r` lands, one
# byte before its own `\n` arrives.
_LINE_END_RE = re.compile(rb"\r\n|\r(?=.)|\n", re.DOTALL)
_STR_LINE_END_RE = re.compile(r"\r\n|\r|\n")

_HIT_CODES = {
    "canary": "guard/canary",
    "private_key": "guard/private_key",
    "guard_error": "guard/error",
}

# A canonical guard_error Hit, built once from the real 002 guard so its sentence
# never drifts out of step with `memory/guard.py`'s own wording.
_GUARD_ERROR_HIT = guard_scan(object(), [])
assert _GUARD_ERROR_HIT is not None and _GUARD_ERROR_HIT.kind == "guard_error"


def _route_label(path: str) -> str:
    if path in ("/mcp", "/mcp/"):
        return "mcp"
    if path == "/session":
        return "session"
    if path == "/healthz":
        return "healthz"
    return "unmatched"


def _expand_nested_json(obj: Any, depth: int = 0) -> Any:
    """A string value that itself parses as a JSON object or array is parsed one
    level deeper (recursively) so a tool result's JSON-inside-JSON text is walked
    for privacy-labelled keys too (spec 3.9)."""
    if depth > 64:
        raise ValueError("nested too deep")
    if isinstance(obj, dict):
        return {key: _expand_nested_json(value, depth + 1) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_expand_nested_json(item, depth + 1) for item in obj]
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            return obj
        if isinstance(parsed, (dict, list)):
            return _expand_nested_json(parsed, depth + 1)
        return obj
    return obj


def _find_event_boundary(buf: bytes) -> int | None:
    """The index just past the terminator ending the blank line that terminates the
    first complete SSE event in `buf`, or `None` if no complete event is buffered
    yet. A line ends with `\\r\\n`, `\\r`, or `\\n` (spec 0, "Emission unit")."""
    prev_end = 0
    for match in _LINE_END_RE.finditer(buf):
        if match.start() == prev_end:
            return match.end()
        prev_end = match.end()
    return None


def _event_data_text(raw_event: bytes) -> str:
    """Every `data:` line (one optional leading space after the colon removed),
    joined with `\\n`."""
    text = raw_event.decode("utf-8", errors="replace")
    parts: list[str] = []
    for line in _STR_LINE_END_RE.split(text):
        if line.startswith("data:"):
            value = line[len("data:") :]
            value = value.removeprefix(" ")
            parts.append(value)
    return "\n".join(parts)


def _jsonrpc_error_payload(hit: Hit) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32603,
            "message": hit.detail,
            "data": {"status": "refused/guard_tripped"},
        },
    }


def _sse_termination_event(hit: Hit) -> bytes:
    data = json.dumps(_jsonrpc_error_payload(hit))
    return f"event: message\r\ndata: {data}\r\n\r\n".encode()


class _CanaryCache:
    """Rebuilds the live `CanarySet` only when `canaries` has changed, via a cheap
    `max(id)` version check, so a token minted after a stream started is still
    caught without re-reading the whole table on every unit (spec 3.9)."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._version: int | None = None
        self._set: CanarySet = canary_set([])

    def current(self) -> CanarySet:
        conn = self._db.connect()
        try:
            row = conn.execute("SELECT max(id) AS v FROM canaries").fetchone()
            version = row["v"]
            if version != self._version:
                self._set = all_canaries(conn)
                self._version = version
        finally:
            conn.close()
        return self._set


@dataclasses.dataclass
class _GuardState:
    start_message: Message | None = None
    is_sse: bool = False
    buffer: bytearray = dataclasses.field(default_factory=bytearray)
    headers_sent: bool = False
    terminated: bool = False


class GuardSeam:
    def __init__(self, app: ASGIApp, db: Database, clock: Callable[[], datetime]) -> None:
        self._app = app
        self._db = db
        self._clock = clock
        self._canary_cache = _CanaryCache(db)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in UNGUARDED_ROUTES:
            await self._app(scope, receive, send)
            return

        state = _GuardState()

        async def guarded_send(message: Message) -> None:
            if state.terminated:
                return
            message_type = message["type"]
            if message_type == "http.response.start":
                state.start_message = message
                headers = {k.lower(): v for k, v in (message.get("headers") or [])}
                state.is_sse = headers.get(b"content-type", b"").startswith(b"text/event-stream")
                return
            if message_type == "http.response.body":
                state.buffer.extend(message.get("body") or b"")
                more_body = bool(message.get("more_body", False))
                if state.is_sse:
                    await self._process_sse(scope, send, state, more_body)
                else:
                    await self._process_json(scope, send, state, more_body)
                return
            await send(message)

        await self._app(scope, receive, guarded_send)

    async def _process_json(
        self, scope: Scope, send: Send, state: _GuardState, more_body: bool
    ) -> None:
        if len(state.buffer) > MAX_BODY_BYTES:
            await self._emit_hit(scope, send, state, _GUARD_ERROR_HIT)
            return
        if more_body:
            return
        text = bytes(state.buffer).decode("utf-8", errors="replace")
        hit = self._scan_unit(text)
        if hit is not None:
            await self._emit_hit(scope, send, state, hit)
            return
        await self._release_start(send, state)
        await send({"type": "http.response.body", "body": bytes(state.buffer), "more_body": False})
        state.terminated = True

    async def _process_sse(
        self, scope: Scope, send: Send, state: _GuardState, more_body: bool
    ) -> None:
        while True:
            boundary = _find_event_boundary(bytes(state.buffer))
            if boundary is None:
                if len(state.buffer) > MAX_EVENT_BYTES:
                    await self._emit_hit(scope, send, state, _GUARD_ERROR_HIT)
                    return
                break
            if boundary > MAX_EVENT_BYTES:
                await self._emit_hit(scope, send, state, _GUARD_ERROR_HIT)
                return

            raw_event = bytes(state.buffer[:boundary])
            del state.buffer[:boundary]
            hit = self._scan_unit(_event_data_text(raw_event))
            if hit is not None:
                await self._emit_hit(scope, send, state, hit)
                return
            if not state.headers_sent:
                await self._release_start(send, state)
            await send({"type": "http.response.body", "body": raw_event, "more_body": True})

        if not more_body:
            if state.buffer:
                await self._emit_hit(scope, send, state, _GUARD_ERROR_HIT)
                return
            if not state.headers_sent:
                await self._release_start(send, state)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            state.terminated = True

    async def _release_start(self, send: Send, state: _GuardState) -> None:
        assert state.start_message is not None
        await send(state.start_message)
        state.headers_sent = True

    async def _emit_hit(self, scope: Scope, send: Send, state: _GuardState, hit: Hit) -> None:
        await self._log_hit(scope, hit)
        if not state.headers_sent:
            body = json.dumps(_jsonrpc_error_payload(hit)).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 409,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
        else:
            await send(
                {
                    "type": "http.response.body",
                    "body": _sse_termination_event(hit),
                    "more_body": False,
                }
            )
        state.terminated = True

    def _scan_unit(self, text: str) -> Hit | None:
        try:
            canaries = self._canary_cache.current()
            hit = guard_scan(text, canaries)
            if hit is not None:
                return hit
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return None
            if not isinstance(parsed, (dict, list)):
                return None
            expanded = _expand_nested_json(parsed)
            return guard_scan(expanded, canaries)
        except Exception:  # noqa: BLE001 - a broken guard aborts rather than serving the response
            return _GUARD_ERROR_HIT

    async def _log_hit(self, scope: Scope, hit: Hit) -> None:
        identity = (scope.get("state") or {}).get("identity")
        actor = identity.id if identity is not None else "anonymous"
        row_scope = identity.scope if identity is not None else "none"
        route = _route_label(scope["path"])
        try:
            with self._db.transaction() as conn:
                ledger_append(
                    conn,
                    now=self._clock(),
                    kind="refused",
                    scope=row_scope,
                    actor=actor,
                    verb=None,
                    tier=None,
                    grant_id=None,
                    record_hash=None,
                    detail={"code": _HIT_CODES[hit.kind], "route": route},
                )
                quotas.increment_counter(conn, "guard_trips")
        except Exception:  # noqa: BLE001 - logged once, with no payload; nothing retries into the seam
            logger.error("guard seam: failed to record a hit")
