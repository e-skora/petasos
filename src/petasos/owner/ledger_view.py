"""A session's ledger rows rendered as roles and plain sentences (spec 4.9). No
`detail_json`, `record_hash`, `prev_hash`, or `hash` is ever returned, and the raw
actor id is never returned.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from petasos.mcp.outcomes import MCP_SENTENCES
from petasos.trust import SENTENCES

_ROLES = ("owner", "agent", "visitor")

_VERB_PHRASES: dict[str, str] = {
    "READ": "A ticket read",
    "ADD-NOTE": "An internal note",
    "CHANGE-RECORD": "A record change",
    "SEND-EMAIL": "An email to the customer",
    "ISSUE-REFUND": "A refund",
    "DELETE-TICKET": "A ticket deletion",
}
_DEFAULT_PHRASE = "An action"

_KIND_TEMPLATES: dict[str, str] = {
    "staged": "{phrase} was requested and is waiting for a person to say yes.",
    "approved": "A person said yes to {phrase_lower}.",
    "executed": "{phrase} ran and was recorded.",
    "aborted": "{phrase} was called off before it ran.",
    "expired": "{phrase} waited too long and lapsed.",
    "failed": "{phrase} could not run, and nothing was changed.",
}

_GUARD_CODES = frozenset({"guard/canary", "guard/private_key", "guard/error"})
_FALLBACK_REFUSAL = "This was refused, and nothing happened."


def _actor_role(scope: str, actor: str) -> str:
    if actor == "system":
        return "system"
    for role in _ROLES:
        if actor == f"{scope}-{role}":
            return role
    return "unknown"


def _phrase(verb: str | None) -> str:
    if verb is None:
        return _DEFAULT_PHRASE
    return _VERB_PHRASES.get(verb, _DEFAULT_PHRASE)


def _refusal_sentence(code: str | None) -> str:
    if code in _GUARD_CODES:
        return MCP_SENTENCES["refused/guard_tripped"]
    if code is not None:
        sentence = SENTENCES.get(f"refused/{code}") or MCP_SENTENCES.get(f"refused/{code}")
        if sentence is not None:
            return sentence
    return _FALLBACK_REFUSAL


def _description(kind: str, code: str | None, verb: str | None) -> str:
    template = _KIND_TEMPLATES.get(kind)
    if template is not None:
        phrase = _phrase(verb)
        if kind == "approved":
            return template.format(phrase_lower=phrase.lower())
        return template.format(phrase=phrase)
    if kind in ("rejected", "refused"):
        return _refusal_sentence(code)
    return _FALLBACK_REFUSAL


def rows_for(
    conn: sqlite3.Connection, *, scope: str, limit: int, before: int | None
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, ts, kind, actor, verb, tier, grant_id, detail_json FROM ledger "
        "WHERE scope = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (scope, before, before, limit),
    ).fetchall()
    rendered = []
    for row in rows:
        detail = json.loads(row["detail_json"])
        code = detail.get("code")
        rendered.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "kind": row["kind"],
                "actor_role": _actor_role(scope, row["actor"]),
                "verb": row["verb"],
                "tier": row["tier"],
                "grant_id": row["grant_id"],
                "code": code,
                "description": _description(row["kind"], code, row["verb"]),
            }
        )
    return rendered


def archived_before_id(conn: sqlite3.Connection) -> int | None:
    for row in conn.execute(
        "SELECT id, detail_json FROM ledger WHERE kind='maintenance' ORDER BY id DESC"
    ):
        detail = json.loads(row["detail_json"])
        if detail.get("code") == "rotated":
            return row["id"]
    return None


def max_id(conn: sqlite3.Connection, *, scope: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM ledger WHERE scope=?", (scope,)
    ).fetchone()
    return row["m"]


def count_aborted_since(conn: sqlite3.Connection, *, scope: str, after_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ledger WHERE scope=? AND kind='aborted' AND id>?",
        (scope, after_id),
    ).fetchone()
    return row["n"]
