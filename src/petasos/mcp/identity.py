"""`Identity` and `ClientRegistry`: per-request authentication keyed by the presented
client id, with a constant-time secret compare (spec 3.1, 3.2).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from petasos.memory.store import MemoryTier

if TYPE_CHECKING:
    from petasos.storage import Database

Role = Literal["visitor", "agent", "owner"]

_DUMMY_DIGEST = hashlib.sha256(b"dummy").hexdigest()

_ROLE_CEILING: dict[Role, MemoryTier] = {
    "visitor": MemoryTier.T0,
    "agent": MemoryTier.T3,
    "owner": MemoryTier.T4,
}
_ROLE_MAY_STAGE: dict[Role, bool] = {"visitor": False, "agent": True, "owner": True}
_ROLE_APPROVER: dict[Role, bool] = {"visitor": False, "agent": False, "owner": True}


@dataclass(frozen=True)
class Identity:
    id: str
    role: Role
    scope: str
    ceiling: MemoryTier
    may_stage: bool
    approver: bool
    expires_at: datetime


class ClientRegistry:
    def __init__(self, db: Database) -> None:
        self._db = db

    def authenticate(self, bearer: str, *, now: datetime) -> Identity | None:
        client_id, sep, secret = bearer.partition(".")
        if not sep:
            hmac.compare_digest(_DUMMY_DIGEST, _DUMMY_DIGEST)
            return None

        conn = self._db.connect()
        try:
            row = conn.execute("SELECT * FROM identities WHERE id=?", (client_id,)).fetchone()
        finally:
            conn.close()

        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if row is None:
            hmac.compare_digest(digest, _DUMMY_DIGEST)
            return None
        if not hmac.compare_digest(digest, row["secret_digest"]):
            return None

        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= now:
            return None

        role: Role = row["role"]
        return Identity(
            id=row["id"],
            role=role,
            scope=row["session_id"],
            ceiling=_ROLE_CEILING[role],
            may_stage=_ROLE_MAY_STAGE[role],
            approver=_ROLE_APPROVER[role],
            expires_at=expires_at,
        )
