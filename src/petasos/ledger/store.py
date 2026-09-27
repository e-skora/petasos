"""Schema for the `ledger` table.

Only this table is chained. `verify()` (see `chain.py`) shows the chain is internally
consistent: each row's hash covers its own content and the previous row's hash, so
editing or deleting a row breaks the chain from that point on. This is tamper
evidence against editing the file through normal means, not proof against a full
rewrite: someone who can rewrite the whole file can recompute every later hash to
match. No ledger row ever holds caller-supplied text (no arguments, no destination,
no unknown tool name); `record_hash` stands in for the content instead.
"""

from __future__ import annotations

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    actor TEXT NOT NULL,
    verb TEXT,
    tier TEXT,
    grant_id INTEGER,
    record_hash TEXT,
    detail_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""
