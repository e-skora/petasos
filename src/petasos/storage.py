"""Database: one SQLite file, WAL mode, explicit migrate(), transaction() = BEGIN IMMEDIATE.

The constructor touches no file. `connect()` and `transaction()` each open a fresh
connection, so no connection is ever shared across threads. `migrate()` creates the
file and every table, explicitly, the first time it runs; each package that owns
tables supplies its own schema, imported here only at call time so this module stays
free of any dependency on the packages built on top of it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def migrate(self) -> None:
        """Create every table. `executescript` issues its own implicit commit, so this
        does not run inside `transaction()`; each statement is idempotent DDL."""
        from petasos.ledger.store import LEDGER_SCHEMA
        from petasos.memory.store import MEMORY_SCHEMA
        from petasos.trust.grants import GRANTS_SCHEMA, RAIL_SLOTS_SCHEMA

        conn = self.connect()
        try:
            conn.executescript(LEDGER_SCHEMA)
            conn.executescript(GRANTS_SCHEMA)
            conn.executescript(RAIL_SLOTS_SCHEMA)
            conn.executescript(MEMORY_SCHEMA)
        finally:
            conn.close()
