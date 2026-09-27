"""The trust core: risk tiers, grants, the ledger-backed executor, and the one entry
point, `Gate.propose`, that MCP and browser callers share.
"""

from __future__ import annotations

from petasos.trust.constraints import (
    HARD_CONSTRAINT_RESOURCE_PREFIXES,
    HARD_CONSTRAINT_TOOLS,
    resource_is_hard_constrained,
    tool_is_hard_constrained,
)
from petasos.trust.executor import Executor
from petasos.trust.gate import Gate
from petasos.trust.grants import Grant, GrantStore
from petasos.trust.outcomes import SENTENCES, Result, make_result
from petasos.trust.record import ActionRecord, canonical_json, record_hash
from petasos.trust.risk import (
    POLICY_VERSION,
    InvalidProfile,
    RiskProfile,
    Tier,
    derive_tier,
    rail_for,
    verb_for,
)
from petasos.trust.tools import InvalidArguments, NotFound, Resolved, ToolDefinition, ToolRegistry

__all__ = [
    "HARD_CONSTRAINT_RESOURCE_PREFIXES",
    "HARD_CONSTRAINT_TOOLS",
    "POLICY_VERSION",
    "SENTENCES",
    "ActionRecord",
    "Executor",
    "Gate",
    "Grant",
    "GrantStore",
    "InvalidArguments",
    "InvalidProfile",
    "NotFound",
    "Resolved",
    "Result",
    "RiskProfile",
    "Tier",
    "ToolDefinition",
    "ToolRegistry",
    "canonical_json",
    "derive_tier",
    "make_result",
    "rail_for",
    "record_hash",
    "resource_is_hard_constrained",
    "tool_is_hard_constrained",
    "verb_for",
]
