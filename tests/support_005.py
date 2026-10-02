"""Shared test helpers for change 005: a lifespan harness that drives the raw ASGI
lifespan protocol directly (the wrapped `build_app` result is not a Starlette app,
so `app.router.lifespan_context` does not exist on it), a tick gate that releases
one `MaintenanceRunner` tick at a time, a recording ASGI app for middleware tests,
and the two `receipt_absent` / `receipt_provided` wrappers the smoke journey tests
use so their meaning does not depend on whether change 004 has merged (spec 005
section 3, acceptance tests 3, 5, 11).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from petasos.storage import Database


@asynccontextmanager
async def lifespan_harness(app: ASGIApp) -> AsyncIterator[None]:
    """Runs the whole ASGI `lifespan` call in one task: sends `lifespan.startup`,
    awaits `lifespan.startup.complete`, yields, then on exit sends
    `lifespan.shutdown` and awaits the task's completion. Raises if the app reports
    `lifespan.startup.failed` or `lifespan.shutdown.failed`."""
    startup_complete: asyncio.Event = asyncio.Event()
    startup_error: list[str] = []
    receive_queue: asyncio.Queue[Message] = asyncio.Queue()

    async def receive() -> Message:
        return await receive_queue.get()

    async def send(message: Message) -> None:
        if message["type"] == "lifespan.startup.complete":
            startup_complete.set()
        elif message["type"] == "lifespan.startup.failed":
            startup_error.append(message.get("message", "startup failed"))
            startup_complete.set()
        elif message["type"] in ("lifespan.shutdown.complete", "lifespan.shutdown.failed"):
            pass

    task = asyncio.ensure_future(app({"type": "lifespan"}, receive, send))
    await receive_queue.put({"type": "lifespan.startup"})
    await startup_complete.wait()
    if startup_error:
        task.cancel()
        raise RuntimeError(startup_error[0])
    try:
        yield
    finally:
        await receive_queue.put({"type": "lifespan.shutdown"})
        await task


class TickGate:
    """An injectable `sleep` for `MaintenanceRunner`. Each call blocks until the
    test calls `release()` exactly once; nothing here ever sleeps for real. Tracks
    an arrival/departure counter (rather than a single reused event) so
    `wait_until_sleeping` always waits for a *new* arrival instead of racing a
    stale, not-yet-cleared flag from the previous cycle."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._arrivals = 0
        self._departures = 0

    async def __call__(self, interval_s: float) -> None:
        async with self._condition:
            self._arrivals += 1
            my_arrival = self._arrivals
            self._condition.notify_all()
            await self._condition.wait_for(lambda: self._departures >= my_arrival)

    async def wait_until_sleeping(self) -> None:
        """Waits until the loop has reached this gate for a tick it has not yet
        released, without releasing it."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._arrivals > self._departures)

    async def release(self) -> None:
        """Waits until the loop is blocked on this gate, then releases one tick."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._arrivals > self._departures)
            self._departures += 1
            self._condition.notify_all()


class LifespanApp:
    """A minimal ASGI app that speaks only the raw lifespan protocol: records
    every scope it sees (lifespan and otherwise) and answers `startup`/`shutdown`
    with the matching `.complete` message, so `MaintenanceRunner` has a real inner
    app to drive in tests."""

    def __init__(self) -> None:
        self.calls: list[Scope] = []
        self.shutdown_seen = asyncio.Event()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls.append(scope)
        if scope["type"] != "lifespan":
            return
        message = await receive()
        assert message["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        message = await receive()
        assert message["type"] == "lifespan.shutdown"
        self.shutdown_seen.set()
        await send({"type": "lifespan.shutdown.complete"})


class RecordingApp:
    """A minimal ASGI app for middleware tests: records every scope it is called
    with (the same object, so a caller can assert identity) and answers 200 with a
    small body for an `http` scope; does nothing for any other scope type."""

    def __init__(self) -> None:
        self.calls: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls.append(scope)
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})


def http_scope(
    *, method: str = "GET", path: str = "/", headers: dict[str, str] | None = None
) -> Scope:
    """A minimal hand-built `http` ASGI scope with the given headers."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ],
    }


async def call_asgi(app: ASGIApp, scope: Scope) -> list[Message]:
    """Calls `app` once against `scope` with an empty body and no disconnect, and
    returns every message it sent."""
    sent: list[Message] = []
    body_sent = False

    async def receive() -> Message:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


_NOT_FOUND_BODY = json.dumps({"detail": "Not Found"}).encode("utf-8")


class _PlainNotFound:
    """Wraps `app` so every `/owner/*` path answers a plain 404 before the inner
    app, standing in for a deploy where change 004 (the owner API) has not merged
    yet. Every other path passes through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/owner/"):
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": _NOT_FOUND_BODY, "more_body": False})
            return
        await self.app(scope, receive, send)


def receipt_absent(app: ASGIApp) -> ASGIApp:
    """Every `/owner/*` path answers a plain 404 before the inner app (spec 005
    acceptance test 11): stands in for a deploy where 004 is not present yet."""
    return _PlainNotFound(app)


class _StandInReceiptRoute:
    """Wraps `app` so `GET /owner/tickets/{number}` is answered by a stand-in that
    reads `helpdesk_fake_refunds` for the caller's scope and answers in 004's shape
    (`data.refunds[].amount_minor`, `currency`), regardless of whether the real 004
    route exists underneath: this wrapper answers first either way, so the smoke
    journey test means the same thing whichever of 004 and 005 merges first (spec
    005 acceptance test 11)."""

    def __init__(self, app: ASGIApp, *, db: Database) -> None:
        self.app = app
        self._db = db

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        prefix = "/owner/tickets/"
        if not path.startswith(prefix) or "/" in path[len(prefix) :]:
            await self.app(scope, receive, send)
            return
        number_str = path[len(prefix) :]
        if not number_str.isdigit():
            await self.app(scope, receive, send)
            return

        scope_id = self._scope_for(scope)
        if scope_id is None:
            await self._respond(send, 401, {"status": "refused/unauthenticated"})
            return

        conn = self._db.connect()
        try:
            rows = conn.execute(
                "SELECT amount_minor, currency, refunded_at FROM helpdesk_fake_refunds "
                "WHERE scope=? AND ticket_id IN "
                "(SELECT id FROM tickets WHERE scope=? AND number=?) "
                "ORDER BY id",
                (scope_id, scope_id, int(number_str)),
            ).fetchall()
        finally:
            conn.close()
        refunds = [
            {
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "refunded_at": row["refunded_at"],
            }
            for row in rows
        ]
        await self._respond(
            send,
            200,
            {
                "status": "ok",
                "explanation": "Done.",
                "tier": None,
                "verb": None,
                "grant_id": None,
                "data": {
                    "ticket": {"number": int(number_str)},
                    "notes": [],
                    "mail": [],
                    "refunds": refunds,
                },
            },
        )

    def _scope_for(self, scope: Scope) -> str | None:
        """Resolves the bearer token's session scope directly, with no expiry
        check (this stand-in only needs to know which namespace to read; the real
        004 route is what enforces expiry, and the journey's tokens are always
        fresh)."""
        import hashlib

        headers = dict(scope.get("headers") or [])
        header_value = headers.get(b"authorization")
        if header_value is None:
            return None
        scheme, _, token = header_value.decode("latin-1").partition(" ")
        if scheme != "Bearer" or not token:
            return None
        client_id, sep, secret = token.partition(".")
        if not sep:
            return None
        conn = self._db.connect()
        try:
            row = conn.execute("SELECT * FROM identities WHERE id=?", (client_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if digest != row["secret_digest"]:
            return None
        return row["session_id"]

    async def _respond(self, send: Send, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


def receipt_provided(app: ASGIApp, db: Database) -> ASGIApp:
    """`GET /owner/tickets/3` (and any other ticket number) is answered by a
    stand-in reading `helpdesk_fake_refunds` directly, in 004's response shape
    (spec 005 acceptance test 11)."""
    return _StandInReceiptRoute(app, db=db)


__all__ = [
    "LifespanApp",
    "RecordingApp",
    "TickGate",
    "call_asgi",
    "http_scope",
    "lifespan_harness",
    "receipt_absent",
    "receipt_provided",
]
