"""Result codes and sentences added by this change (spec 3.24). 001's sentence
table is 001's wall; these are MCP-layer outcomes that never reach `trust/outcomes.py`.
"""

from __future__ import annotations

MCP_SENTENCES: dict[str, str] = {
    "ok": "Done.",
    "refused/not_allowed": "That tool is not available to this identity.",
    "refused/quota": "This session has used its hourly allowance; try again later.",
    "refused/ceiling": "The demo is at capacity; try again later.",
    "refused/guard_tripped": (
        "Private memory was about to leave the system, so the response was stopped."
    ),
}
