"""SessionStore: mint a visitor session (three identities, a namespace, one hour of
life) as one transaction, and expire sessions past their hour, deleting their
namespace's rows everywhere except the ledger and the global counters (spec 3.19,
3.20).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from petasos.helpdesk.data import seed_session
from petasos.memory.store import MemoryTier
from petasos.sessions import quotas

if TYPE_CHECKING:
    from petasos.storage import Database

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    secret_digest TEXT NOT NULL,
    ceiling TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

_ROLES: tuple[str, ...] = ("visitor", "agent", "owner")
_CEILINGS: dict[str, MemoryTier] = {
    "visitor": MemoryTier.T0,
    "agent": MemoryTier.T3,
    "owner": MemoryTier.T4,
}

SESSION_TTL = timedelta(hours=1)


class MintQuotaExceeded(Exception):
    pass


class MintCeilingExceeded(Exception):
    pass


@dataclass(frozen=True)
class Minted:
    session: str
    expires_at: datetime
    tokens: dict[str, str]


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class SessionStore:
    def __init__(self, db: Database, *, clock: Callable[[], datetime]) -> None:
        self._db = db
        self._clock = clock

    def mint(self) -> Minted:
        now = self._clock()
        self.expire(now)

        with self._db.transaction() as conn:
            if quotas.live_session_count(conn, now=now) >= quotas.MAX_LIVE_SESSIONS:
                raise MintCeilingExceeded()
            if not quotas.reserve(
                conn,
                scope="global",
                family="mint",
                limit=quotas.MINTS_PER_HOUR,
                window=timedelta(hours=1),
                now=now,
            ):
                raise MintQuotaExceeded()
            if not quotas.reserve(
                conn,
                scope="global",
                family="mint_day",
                limit=quotas.MINTS_PER_DAY,
                window=timedelta(hours=24),
                now=now,
            ):
                raise MintQuotaExceeded()

            session_id = "s-" + secrets.token_urlsafe(12)
            expires_at = now + SESSION_TTL
            conn.execute(
                "INSERT INTO sessions (id, created_at, expires_at) VALUES (?, ?, ?)",
                (session_id, now.isoformat(), expires_at.isoformat()),
            )

            tokens: dict[str, str] = {}
            for role in _ROLES:
                identity_id = f"{session_id}-{role}"
                secret = secrets.token_urlsafe(24)
                conn.execute(
                    "INSERT INTO identities "
                    "(id, session_id, role, secret_digest, ceiling, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        identity_id,
                        session_id,
                        role,
                        _digest(secret),
                        _CEILINGS[role].value,
                        expires_at.isoformat(),
                    ),
                )
                tokens[role] = f"{identity_id}.{secret}"

            seed_session(conn, self._db, session_id, self._clock)
            quotas.increment_counter(conn, "sessions_minted")

        return Minted(session=session_id, expires_at=expires_at, tokens=tokens)

    def expire(self, now: datetime) -> int:
        """Delete every row of each expired session's scope (never the global
        counters or the ledger). The boundary instant counts as expired."""
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE expires_at<=?", (now.isoformat(),)
            ).fetchall()
            scopes = [row["id"] for row in rows]
            for scope in scopes:
                conn.execute("DELETE FROM tickets WHERE scope=?", (scope,))
                conn.execute("DELETE FROM notes WHERE scope=?", (scope,))
                conn.execute("DELETE FROM helpdesk_fake_mail WHERE scope=?", (scope,))
                conn.execute("DELETE FROM helpdesk_fake_refunds WHERE scope=?", (scope,))
                conn.execute("DELETE FROM memory_entries WHERE scope=?", (scope,))
                conn.execute("DELETE FROM grants WHERE scope=?", (scope,))
                conn.execute("DELETE FROM rail_slots WHERE scope=?", (scope,))
                conn.execute("DELETE FROM quota_events WHERE scope=?", (scope,))
                conn.execute("DELETE FROM identities WHERE session_id=?", (scope,))
                conn.execute("DELETE FROM sessions WHERE id=?", (scope,))
            return len(scopes)
