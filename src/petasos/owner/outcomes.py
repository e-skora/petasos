"""Result codes and sentences added by this change for the owner API (spec 4.6).
001's and 003's sentence tables are those changes' walls; these are owner-layer-only
outcomes that never reach `trust/outcomes.py` or `mcp/outcomes.py`.
"""

from __future__ import annotations

OWNER_SENTENCES: dict[str, str] = {
    "refused/unauthenticated": "Sign in to this demo session first.",
    "refused/origin": "This website is not allowed to use the demo's browser endpoints.",
    "refused/too_large": "That request is too large for this demo.",
}
