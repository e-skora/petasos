"""petasos.memory: a tiered private memory store (T0 public to T5 private-local), one
canary planted for life in every T4-or-above entry, and a destination-blind guard that
scans an outgoing payload for a canary or a privacy-labelled key and aborts on a hit
(spec 002-memory-canary-guard). This package never imports `petasos.trust`: the memory
tier ladder (T0 to T5) shares no code with the risk tier ladder (L1 to L5).
"""

from __future__ import annotations

from petasos.memory.canary import CanarySet, all_canaries, canary_entry, canary_set, mint_canary
from petasos.memory.guard import (
    MAX_DEPTH,
    MAX_NODES,
    MAX_TEXT_BYTES,
    GuardTripped,
    Hit,
    assert_no_private_payload,
    scan,
)
from petasos.memory.store import (
    MEMORY_SCHEMA,
    Entry,
    InvalidEntry,
    MemoryStore,
    MemoryTier,
    TierLocked,
)

__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_TEXT_BYTES",
    "MEMORY_SCHEMA",
    "CanarySet",
    "Entry",
    "GuardTripped",
    "Hit",
    "InvalidEntry",
    "MemoryStore",
    "MemoryTier",
    "TierLocked",
    "all_canaries",
    "assert_no_private_payload",
    "canary_entry",
    "canary_set",
    "mint_canary",
    "scan",
]
