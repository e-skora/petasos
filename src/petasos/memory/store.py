"""MemoryTier, Entry, and MemoryStore: tiered private memory with one expiry rule
shared by `put` and `put_in` (spec 002-memory-canary-guard, 2.1 to 2.5, 2.7 to 2.10).

`petasos.memory` never imports `petasos.trust`: memory tiers (T0 to T5) are a
different ladder from the risk tiers (L1 to L5) and share no code.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from functools import total_ordering
from typing import TYPE_CHECKING

from petasos.memory.canary import marker_for, register_canary, strip_marker

if TYPE_CHECKING:
    from petasos.storage import Database

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    tier TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    canary_id INTEGER,
    UNIQUE(scope, key)
);
CREATE TABLE IF NOT EXISTS canaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    entry_id INTEGER NOT NULL,
    minted_at TEXT NOT NULL
);
"""

_MAX_KEY_LEN = 128
_MAX_TEXT_LEN = 8192


class InvalidEntry(Exception):
    pass


class TierLocked(Exception):
    pass


@total_ordering
class MemoryTier(Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, MemoryTier):
            return NotImplemented
        return _TIER_RANK[self] < _TIER_RANK[other]


_TIER_RANK = {tier: rank for rank, tier in enumerate(MemoryTier)}


@dataclasses.dataclass(frozen=True)
class Entry:
    id: int
    scope: str
    key: str
    tier: MemoryTier
    text: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    canary: str | None


_SELECT_BY_KEY = (
    "SELECT m.*, c.token AS canary_token FROM memory_entries m "
    "LEFT JOIN canaries c ON m.canary_id = c.id WHERE m.scope=? AND m.key=?"
)


def _row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        scope=row["scope"],
        key=row["key"],
        tier=MemoryTier(row["tier"]),
        text=row["text"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        canary=row["canary_token"],
    )


class MemoryStore:
    def __init__(
        self,
        db: Database,
        *,
        scope: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._scope = scope
        self._clock = clock

    def _sweep_in(self, conn: sqlite3.Connection) -> None:
        now = self._clock()
        conn.execute(
            "DELETE FROM memory_entries WHERE scope=? AND expires_at IS NOT NULL AND expires_at<=?",
            (self._scope, now.isoformat()),
        )

    def sweep(self) -> None:
        with self._db.transaction() as conn:
            self._sweep_in(conn)

    def put(
        self,
        key: str,
        text: str,
        tier: MemoryTier,
        *,
        expires_at: datetime | None = None,
    ) -> Entry:
        with self._db.transaction() as conn:
            self._sweep_in(conn)
            return self.put_in(conn, key, text, tier, expires_at=expires_at)

    def put_in(
        self,
        conn: sqlite3.Connection,
        key: str,
        text: str,
        tier: MemoryTier,
        *,
        expires_at: datetime | None = None,
    ) -> Entry:
        now = self._clock()
        if not key or len(key) > _MAX_KEY_LEN:
            raise InvalidEntry(f"key must be 1 to {_MAX_KEY_LEN} characters")
        if not text:
            raise InvalidEntry("text must not be empty")
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise InvalidEntry("expires_at must be timezone-aware")
            if expires_at <= now:
                raise InvalidEntry("expires_at must be later than now")

        row = conn.execute(_SELECT_BY_KEY, (self._scope, key)).fetchone()
        live = row is not None and (
            row["expires_at"] is None or datetime.fromisoformat(row["expires_at"]) > now
        )
        old_canary: str | None = row["canary_token"] if live else None

        if live and old_canary is not None and tier < MemoryTier.T4:
            raise TierLocked(f"{key!r} has a canary and cannot be lowered below T4")

        stripped = strip_marker(text, old_canary) if live else text
        if len(stripped) > _MAX_TEXT_LEN:
            raise InvalidEntry(f"text must be at most {_MAX_TEXT_LEN} characters")

        expires_at_s = expires_at.isoformat() if expires_at is not None else None

        if live:
            entry_id = row["id"]
            created_at = datetime.fromisoformat(row["created_at"])
            if tier >= MemoryTier.T4:
                if old_canary is not None:
                    token: str | None = old_canary
                    canary_id = row["canary_id"]
                else:
                    canary_id, token = register_canary(
                        conn, scope=self._scope, entry_id=entry_id, now=now
                    )
                stored_text = stripped + marker_for(token)
            else:
                canary_id, token = None, None
                stored_text = stripped
            conn.execute(
                "UPDATE memory_entries SET tier=?, text=?, updated_at=?, expires_at=?, canary_id=? "
                "WHERE id=?",
                (tier.value, stored_text, now.isoformat(), expires_at_s, canary_id, entry_id),
            )
            return Entry(
                id=entry_id,
                scope=self._scope,
                key=key,
                tier=tier,
                text=stored_text,
                created_at=created_at,
                updated_at=now,
                expires_at=expires_at,
                canary=token,
            )

        if row is not None:
            conn.execute("DELETE FROM memory_entries WHERE id=?", (row["id"],))

        cur = conn.execute(
            "INSERT INTO memory_entries "
            "(scope, key, tier, text, created_at, updated_at, expires_at, canary_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                self._scope,
                key,
                tier.value,
                stripped,
                now.isoformat(),
                now.isoformat(),
                expires_at_s,
            ),
        )
        entry_id = cur.lastrowid
        token = None
        stored_text = stripped
        if tier >= MemoryTier.T4:
            canary_id, token = register_canary(conn, scope=self._scope, entry_id=entry_id, now=now)
            stored_text = stripped + marker_for(token)
            conn.execute(
                "UPDATE memory_entries SET text=?, canary_id=? WHERE id=?",
                (stored_text, canary_id, entry_id),
            )
        return Entry(
            id=entry_id,
            scope=self._scope,
            key=key,
            tier=tier,
            text=stored_text,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            canary=token,
        )

    def read(self, key: str, *, ceiling: MemoryTier) -> Entry | None:
        with self._db.transaction() as conn:
            self._sweep_in(conn)
            row = conn.execute(_SELECT_BY_KEY, (self._scope, key)).fetchone()
        if row is None:
            return None
        entry = _row_to_entry(row)
        if entry.tier > ceiling:
            return None
        return entry

    def search(self, query: str, *, ceiling: MemoryTier, limit: int = 20) -> list[Entry]:
        if not (1 <= limit <= 100):
            raise InvalidEntry("limit must be between 1 and 100")
        with self._db.transaction() as conn:
            self._sweep_in(conn)
            rows = conn.execute(
                "SELECT m.*, c.token AS canary_token FROM memory_entries m "
                "LEFT JOIN canaries c ON m.canary_id = c.id "
                "WHERE m.scope=? ORDER BY m.updated_at DESC, m.id DESC",
                (self._scope,),
            ).fetchall()
        query_l = query.lower()
        results: list[Entry] = []
        for row in rows:
            entry = _row_to_entry(row)
            if entry.tier > ceiling:
                continue
            if query_l not in entry.key.lower() and query_l not in entry.text.lower():
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results
