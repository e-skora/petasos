"""Hard constraints: checked first in `Gate.propose`, before the registry lookup, and
again on the resolved resource. Above the ladder; nothing in a request can unlock them.
"""

from __future__ import annotations

HARD_CONSTRAINT_TOOLS = frozenset({"disable_gate", "rotate_canaries", "edit_ledger"})
HARD_CONSTRAINT_RESOURCE_PREFIXES = ("ledger:", "grant:", "canary:", "config:")


def tool_is_hard_constrained(name: str) -> bool:
    return name in HARD_CONSTRAINT_TOOLS


def resource_is_hard_constrained(resource: str) -> bool:
    return resource.startswith(HARD_CONSTRAINT_RESOURCE_PREFIXES)
