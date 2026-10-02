"""Browser endpoints for the demo app: the second front door to the same `Gate`,
`GrantStore`, `Executor`, and `Ledger` the MCP tools use (spec 4.1, 4.4, 4.5, 4.6).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from petasos.helpdesk import ReadCapture
from petasos.helpdesk.data import CANARY_TICKET_NUMBER
from petasos.ledger import Ledger
from petasos.mcp.identity import ClientRegistry, Identity
from petasos.mcp.outcomes import MCP_SENTENCES
from petasos.mcp.service import PROPOSABLE_TOOLS, approve_and_run, shape_ok
from petasos.mcp.tools import GateFactory, result_json
from petasos.owner import ledger_view, reads
from petasos.owner.auth import Unauthenticated, identity_for
from petasos.owner.cards import owner_card
from petasos.owner.outcomes import OWNER_SENTENCES
from petasos.sessions import quotas
from petasos.trust import SENTENCES

if TYPE_CHECKING:
    from petasos.storage import Database

MAX_OWNER_BODY_BYTES = 16_384
BROWSER_READS_PER_HOUR = 1200
VERIFY_PER_HOUR = 30

_ALL_ROLES = frozenset({"visitor", "agent", "owner"})
_WRITE_ROLES = frozenset({"agent", "owner"})
_OWNER_ONLY = frozenset({"owner"})

_TICKET_NUMBER_RE = re.compile(r"^[0-9]{1,9}$")
_LIMIT_RE = re.compile(r"^[0-9]{1,3}$")
_BEFORE_RE = re.compile(r"^[0-9]{1,12}$")


def _sentence_for(code: str) -> str:
    return OWNER_SENTENCES.get(code) or MCP_SENTENCES.get(code) or SENTENCES[code]


def _refusal(status_code: int, code: str) -> JSONResponse:
    payload = {
        "status": code,
        "explanation": _sentence_for(code),
        "tier": None,
        "verb": None,
        "grant_id": None,
    }
    return JSONResponse(payload, status_code=status_code)


def _ok(data: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "explanation": MCP_SENTENCES["ok"],
        "tier": None,
        "verb": None,
        "grant_id": None,
        "data": data,
    }


def _authenticate_and_authorize(
    request: Request,
    *,
    registry: ClientRegistry,
    clock: Callable[[], datetime],
    allowed_roles: frozenset[str],
) -> tuple[Identity | None, JSONResponse | None]:
    try:
        identity = identity_for(request, registry=registry, clock=clock)
    except Unauthenticated:
        return None, _refusal(401, "refused/unauthenticated")
    if identity.role not in allowed_roles:
        return None, _refusal(403, "refused/not_allowed")
    return identity, None


def _read_body(request: Request) -> bytes | None:
    """Returns the body bytes, or `None` when the bound is exceeded (spec 4.4)."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError:
            length = None
        if length is not None:
            if length > MAX_OWNER_BODY_BYTES:
                return None
            return anyio.from_thread.run(request.body)

    async def _accumulate() -> bytes | None:
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_OWNER_BODY_BYTES:
                return None
        return b"".join(chunks)

    return anyio.from_thread.run(_accumulate)


def _parse_json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_json_object_allow_absent(raw: bytes) -> dict[str, Any] | None:
    if raw == b"":
        return {}
    return _parse_json_object(raw)


def _is_bounded_string(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 200


def _parse_ledger_query(request: Request) -> tuple[int, int | None] | None:
    qp = request.query_params
    if set(qp.keys()) - {"limit", "before"}:
        return None
    if len(qp.getlist("limit")) > 1 or len(qp.getlist("before")) > 1:
        return None

    limit_raw = qp.get("limit")
    if limit_raw is None:
        limit = 50
    else:
        if not _LIMIT_RE.match(limit_raw):
            return None
        limit = int(limit_raw)
        if not (1 <= limit <= 200):
            return None

    before_raw = qp.get("before")
    before: int | None = None
    if before_raw is not None:
        if not _BEFORE_RE.match(before_raw):
            return None
        before = int(before_raw)

    return limit, before


def _reserve(
    db: Database,
    *,
    scope: str,
    family: str,
    limit: int,
    now: datetime,
    count_tool_call: bool = False,
) -> bool:
    with db.transaction() as conn:
        admitted = quotas.reserve(
            conn, scope=scope, family=family, limit=limit, window=timedelta(hours=1), now=now
        )
        if admitted and count_tool_call:
            quotas.increment_counter(conn, "tool_calls")
    return admitted


def owner_routes(
    db: Database, *, registry: ClientRegistry, clock: Callable[[], datetime]
) -> list[Route]:
    factory = GateFactory(db, clock)

    def owner_me(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_ALL_ROLES
        )
        if error is not None:
            return error
        now = clock()
        seconds_left = max(0, int((identity.expires_at - now).total_seconds()))
        data = {
            "role": identity.role,
            "session": identity.scope,
            "expires_at": identity.expires_at.isoformat(),
            "seconds_left": seconds_left,
            "hidden_ticket_number": CANARY_TICKET_NUMBER,
        }
        return JSONResponse(_ok(data))

    def owner_tickets(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_ALL_ROLES
        )
        if error is not None:
            return error
        if not _reserve(
            db,
            scope=identity.scope,
            family="calls",
            limit=quotas.CALLS_PER_HOUR,
            now=clock(),
            count_tool_call=True,
        ):
            return _refusal(429, "refused/quota")
        capture = ReadCapture()
        gate = factory.for_call(identity, capture)
        result = gate.propose("list_tickets", {}, proposer=identity.id)
        if result.code == "executed":
            return JSONResponse(_ok(capture.data))
        return JSONResponse(result_json(result))

    def owner_ticket_detail(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_ALL_ROLES
        )
        if error is not None:
            return error
        number_str = request.path_params["number"]
        if not _TICKET_NUMBER_RE.match(number_str) or int(number_str) <= 0:
            return _refusal(404, "refused/invalid_arguments")
        number = int(number_str)
        if not _reserve(
            db,
            scope=identity.scope,
            family="calls",
            limit=quotas.CALLS_PER_HOUR,
            now=clock(),
            count_tool_call=True,
        ):
            return _refusal(429, "refused/quota")
        capture = ReadCapture()
        gate = factory.for_call(identity, capture)
        result = gate.propose("get_ticket", {"ticket": number}, proposer=identity.id)
        if result.code != "executed":
            return JSONResponse(result_json(result))
        ticket = dict(capture.data) if capture.data is not None else {}
        for key in ("notes", "mail", "refunds"):
            ticket.pop(key, None)
        conn = db.connect()
        try:
            activity = reads.ticket_activity(conn, scope=identity.scope, number=number)
        finally:
            conn.close()
        data = {"ticket": ticket, **activity}
        return JSONResponse(_ok(data))

    def owner_propose(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_WRITE_ROLES
        )
        if error is not None:
            return error
        raw = _read_body(request)
        if raw is None:
            return _refusal(413, "refused/too_large")
        parsed = _parse_json_object(raw)
        if parsed is None or set(parsed) != {"tool", "arguments"}:
            return _refusal(400, "refused/invalid_arguments")
        tool, arguments = parsed["tool"], parsed["arguments"]
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            return _refusal(400, "refused/invalid_arguments")
        if tool not in PROPOSABLE_TOOLS:
            return _refusal(403, "refused/not_allowed")
        if not shape_ok(tool, arguments):
            return _refusal(400, "refused/invalid_arguments")
        if not _reserve(
            db,
            scope=identity.scope,
            family="calls",
            limit=quotas.CALLS_PER_HOUR,
            now=clock(),
            count_tool_call=True,
        ):
            return _refusal(429, "refused/quota")
        gate = factory.for_call(identity, ReadCapture())
        result = gate.propose(tool, arguments, proposer=identity.id)
        return JSONResponse(result_json(result))

    def owner_approvals(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_OWNER_ONLY
        )
        if error is not None:
            return error
        if not identity.approver:
            return _refusal(403, "refused/not_allowed")
        if not _reserve(
            db,
            scope=identity.scope,
            family="browser_reads",
            limit=BROWSER_READS_PER_HOUR,
            now=clock(),
        ):
            return _refusal(429, "refused/quota")
        gate = factory.for_call(identity, ReadCapture())
        grants = sorted(
            gate.grants.list_pending(viewer=identity.id), key=lambda g: g.id, reverse=True
        )
        conn = db.connect()
        try:
            cards = [
                owner_card(
                    grant,
                    meta=reads.ticket_meta(
                        conn, scope=identity.scope, resource=grant.record.resource
                    ),
                )
                for grant in grants
            ]
        finally:
            conn.close()
        return JSONResponse(_ok(cards))

    def owner_approve(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_OWNER_ONLY
        )
        if error is not None:
            return error
        raw = _read_body(request)
        if raw is None:
            return _refusal(413, "refused/too_large")
        parsed = _parse_json_object(raw)
        if parsed is None or set(parsed) != {"token", "verb"}:
            return _refusal(400, "refused/invalid_arguments")
        token, verb = parsed["token"], parsed["verb"]
        if not _is_bounded_string(token) or not _is_bounded_string(verb):
            return _refusal(400, "refused/invalid_arguments")
        if not identity.approver:
            return _refusal(403, "refused/not_allowed")
        if not _reserve(
            db,
            scope=identity.scope,
            family="calls",
            limit=quotas.CALLS_PER_HOUR,
            now=clock(),
            count_tool_call=True,
        ):
            return _refusal(429, "refused/quota")
        gate = factory.for_call(identity, ReadCapture())
        result = approve_and_run(gate, token=token, verb=verb, approver=identity.id)
        return JSONResponse(result_json(result))

    def owner_abort(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_OWNER_ONLY
        )
        if error is not None:
            return error
        raw = _read_body(request)
        if raw is None:
            return _refusal(413, "refused/too_large")
        parsed = _parse_json_object(raw)
        if parsed is None or set(parsed) != {"token"}:
            return _refusal(400, "refused/invalid_arguments")
        token = parsed["token"]
        if not _is_bounded_string(token):
            return _refusal(400, "refused/invalid_arguments")
        if not identity.approver:
            return _refusal(403, "refused/not_allowed")
        if not _reserve(
            db,
            scope=identity.scope,
            family="calls",
            limit=quotas.CALLS_PER_HOUR,
            now=clock(),
            count_tool_call=True,
        ):
            return _refusal(429, "refused/quota")
        gate = factory.for_call(identity, ReadCapture())
        result = gate.grants.abort(token=token, approver=identity.id)
        return JSONResponse(result_json(result))

    def owner_abort_all(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_OWNER_ONLY
        )
        if error is not None:
            return error
        raw = _read_body(request)
        if raw is None:
            return _refusal(413, "refused/too_large")
        parsed = _parse_json_object_allow_absent(raw)
        if parsed is None or parsed != {}:
            return _refusal(400, "refused/invalid_arguments")
        if not identity.approver:
            return _refusal(403, "refused/not_allowed")
        if not _reserve(
            db,
            scope=identity.scope,
            family="calls",
            limit=quotas.CALLS_PER_HOUR,
            now=clock(),
            count_tool_call=True,
        ):
            return _refusal(429, "refused/quota")
        conn = db.connect()
        try:
            before_id = ledger_view.max_id(conn, scope=identity.scope)
        finally:
            conn.close()
        gate = factory.for_call(identity, ReadCapture())
        result = gate.grants.abort_all(approver=identity.id)
        conn = db.connect()
        try:
            aborted_count = ledger_view.count_aborted_since(
                conn, scope=identity.scope, after_id=before_id
            )
        finally:
            conn.close()
        return JSONResponse(result_json(result, data={"aborted": aborted_count}))

    def owner_ledger(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_ALL_ROLES
        )
        if error is not None:
            return error
        parsed_query = _parse_ledger_query(request)
        if parsed_query is None:
            return _refusal(400, "refused/invalid_arguments")
        limit, before = parsed_query
        if not _reserve(
            db,
            scope=identity.scope,
            family="browser_reads",
            limit=BROWSER_READS_PER_HOUR,
            now=clock(),
        ):
            return _refusal(429, "refused/quota")
        conn = db.connect()
        try:
            rows = ledger_view.rows_for(conn, scope=identity.scope, limit=limit, before=before)
            archived = ledger_view.archived_before_id(conn)
        finally:
            conn.close()
        return JSONResponse(_ok({"rows": rows, "archived_before_id": archived}))

    def owner_ledger_verify(request: Request) -> JSONResponse:
        identity, error = _authenticate_and_authorize(
            request, registry=registry, clock=clock, allowed_roles=_ALL_ROLES
        )
        if error is not None:
            return error
        raw = _read_body(request)
        if raw is None:
            return _refusal(413, "refused/too_large")
        parsed = _parse_json_object_allow_absent(raw)
        if parsed is None or parsed != {}:
            return _refusal(400, "refused/invalid_arguments")
        if not _reserve(
            db, scope=identity.scope, family="verify", limit=VERIFY_PER_HOUR, now=clock()
        ):
            return _refusal(429, "refused/quota")

        first_bad = Ledger(db).verify()
        conn = db.connect()
        try:
            rows_count = conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"]
            archived = ledger_view.archived_before_id(conn)
        finally:
            conn.close()

        if first_bad is None:
            if archived is None:
                explanation = "Every row checks out. The chain is internally consistent."
            else:
                explanation = (
                    "Every row since the last archive checks out. "
                    "Older rows were moved to an archive file."
                )
            data: dict[str, Any] = {"ok": True, "rows": rows_count, "archived_before_id": archived}
        else:
            explanation = (
                f"Row {first_bad} does not match what it should. "
                "Something was changed after it was written."
            )
            data = {
                "ok": False,
                "first_bad_row": first_bad,
                "rows": rows_count,
                "archived_before_id": archived,
            }

        payload = {
            "status": "ok",
            "explanation": explanation,
            "tier": None,
            "verb": None,
            "grant_id": None,
            "data": data,
        }
        return JSONResponse(payload)

    return [
        Route("/owner/me", endpoint=owner_me, methods=["GET"]),
        Route("/owner/tickets", endpoint=owner_tickets, methods=["GET"]),
        Route("/owner/tickets/{number}", endpoint=owner_ticket_detail, methods=["GET"]),
        Route("/owner/propose", endpoint=owner_propose, methods=["POST"]),
        Route("/owner/approvals", endpoint=owner_approvals, methods=["GET"]),
        Route("/owner/approve", endpoint=owner_approve, methods=["POST"]),
        Route("/owner/abort", endpoint=owner_abort, methods=["POST"]),
        Route("/owner/abort-all", endpoint=owner_abort_all, methods=["POST"]),
        Route("/owner/ledger", endpoint=owner_ledger, methods=["GET"]),
        Route("/owner/ledger/verify", endpoint=owner_ledger_verify, methods=["POST"]),
    ]
