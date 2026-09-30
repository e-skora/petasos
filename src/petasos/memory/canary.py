"""Canary tokens: mint once per T4-or-above entry, never re-mint, and outlive the
entry they were planted in (spec 002-memory-canary-guard, 2.5 to 2.7, 2.14).
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from collections.abc import Iterable
from datetime import datetime

_TOKEN_RE = re.compile(r"^cn-[A-Za-z0-9_-]{22}$")


def mint_canary() -> str:
    return "cn-" + secrets.token_urlsafe(16)


def marker_for(token: str) -> str:
    return f" [ref {token}]"


def strip_marker(text: str, token: str | None) -> str:
    if token is None:
        return text
    return text.replace(marker_for(token), "")


class CanarySet(frozenset):
    pass


def canary_set(tokens: Iterable[str]) -> CanarySet:
    values = list(tokens)
    for token in values:
        if not isinstance(token, str) or not _TOKEN_RE.match(token):
            raise ValueError(f"malformed canary token: {token!r}")
    return CanarySet(values)


def register_canary(
    conn: sqlite3.Connection, *, scope: str, entry_id: int, now: datetime
) -> tuple[int, str]:
    """Mint and register one canary for `entry_id`, retrying once on a token
    collision (spec 2.6) before letting the second failure raise."""
    token = mint_canary()
    try:
        cur = conn.execute(
            "INSERT INTO canaries (token, scope, entry_id, minted_at) VALUES (?, ?, ?, ?)",
            (token, scope, entry_id, now.isoformat()),
        )
    except sqlite3.IntegrityError:
        token = mint_canary()
        cur = conn.execute(
            "INSERT INTO canaries (token, scope, entry_id, minted_at) VALUES (?, ?, ?, ?)",
            (token, scope, entry_id, now.isoformat()),
        )
    return cur.lastrowid, token


def all_canaries(conn: sqlite3.Connection) -> CanarySet:
    rows = conn.execute("SELECT token FROM canaries").fetchall()
    return canary_set(row["token"] for row in rows)


def canary_entry(conn: sqlite3.Connection, token: str) -> tuple[int, str] | None:
    row = conn.execute("SELECT entry_id, scope FROM canaries WHERE token=?", (token,)).fetchone()
    if row is None:
        return None
    return row["entry_id"], row["scope"]
