"""append(conn, ...) writes one ledger row inside the caller's transaction; verify()
names the first row whose hash or previous-hash link breaks.

This module depends on nothing outside the standard library, so `petasos.trust` (which
appends to the ledger) can depend on `petasos.ledger` without a cycle. It uses its own
canonical JSON form (the same rule as `petasos.trust.record.canonical_json`: sorted
keys, no separators, no NaN, no float) so a row's hash is reproducible without
importing anything from `petasos.trust`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from hashlib import sha256
from typing import Any

GENESIS_HASH = "0" * 64


def _reject_float(obj: Any) -> None:
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in a ledger row")
    if isinstance(obj, Mapping):
        for value in obj.values():
            _reject_float(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _reject_float(value)


def _canonical_json(obj: Any) -> bytes:
    _reject_float(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _content_hash(
    *,
    ts: str,
    kind: str,
    scope: str,
    actor: str,
    verb: str | None,
    tier: str | None,
    grant_id: int | None,
    record_hash: str | None,
    detail: Mapping[str, Any],
    prev_hash: str,
) -> str:
    payload = {
        "ts": ts,
        "kind": kind,
        "scope": scope,
        "actor": actor,
        "verb": verb,
        "tier": tier,
        "grant_id": grant_id,
        "record_hash": record_hash,
        "detail": detail,
        "prev_hash": prev_hash,
    }
    return sha256(_canonical_json(payload)).hexdigest()


def append(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    kind: str,
    scope: str,
    actor: str,
    verb: str | None,
    tier: str | None,
    grant_id: int | None,
    record_hash: str | None,
    detail: Mapping[str, Any],
) -> None:
    """Append one row. Must run inside a transaction the caller opened (BEGIN
    IMMEDIATE), so the read of the previous row's hash and this insert are atomic
    with respect to every other writer."""
    ts = now.isoformat()
    prev_row = conn.execute("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = prev_row["hash"] if prev_row is not None else GENESIS_HASH
    row_hash = _content_hash(
        ts=ts,
        kind=kind,
        scope=scope,
        actor=actor,
        verb=verb,
        tier=tier,
        grant_id=grant_id,
        record_hash=record_hash,
        detail=detail,
        prev_hash=prev_hash,
    )
    conn.execute(
        "INSERT INTO ledger "
        "(ts, kind, scope, actor, verb, tier, grant_id, record_hash, detail_json, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts,
            kind,
            scope,
            actor,
            verb,
            tier,
            grant_id,
            record_hash,
            _canonical_json(detail).decode("utf-8"),
            prev_hash,
            row_hash,
        ),
    )


def verify(conn: sqlite3.Connection) -> int | None:
    """Walk the ledger in id order. Return the id of the first row whose own hash does
    not match its content, or whose `prev_hash` does not match the previous row's
    actual hash (which also catches a deleted or reordered row). `None` means the
    chain is intact from the genesis row (whose `prev_hash` must be all zeros)."""
    rows: Sequence[sqlite3.Row] = conn.execute("SELECT * FROM ledger ORDER BY id ASC").fetchall()
    expected_prev = GENESIS_HASH
    for row in rows:
        detail = json.loads(row["detail_json"])
        expected_hash = _content_hash(
            ts=row["ts"],
            kind=row["kind"],
            scope=row["scope"],
            actor=row["actor"],
            verb=row["verb"],
            tier=row["tier"],
            grant_id=row["grant_id"],
            record_hash=row["record_hash"],
            detail=detail,
            prev_hash=row["prev_hash"],
        )
        if row["hash"] != expected_hash:
            return row["id"]
        if row["prev_hash"] != expected_prev:
            return row["id"]
        expected_prev = row["hash"]
    return None
