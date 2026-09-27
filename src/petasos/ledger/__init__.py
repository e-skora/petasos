"""The append-only, hash-chained audit log. See `store.py` for the schema and what the
chain proves, and `chain.py` for `append` and `verify`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from petasos.ledger.chain import append, verify

if TYPE_CHECKING:
    from petasos.storage import Database

__all__ = ["Ledger", "append", "verify"]


class Ledger:
    """A thin read handle: `Ledger(db).verify()` opens its own connection."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def verify(self) -> int | None:
        conn = self._db.connect()
        try:
            return verify(conn)
        finally:
            conn.close()
