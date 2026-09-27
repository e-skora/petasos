"""ActionRecord, canonical_json, record_hash.

The action record is the exact action a person approves: frozen at proposal time, and
hashed so tampering with the stored copy is detectable (spec 1.6, 1.7).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class ActionRecord:
    tool: str
    arguments: Mapping[str, Any]
    destination: str | None
    amount_minor: int | None
    currency: str | None
    resource: str
    resource_version: int
    proposer: str
    policy_version: str
    scope: str

    def __post_init__(self) -> None:
        if (self.amount_minor is None) != (self.currency is None):
            raise ValueError("amount_minor and currency must be set together")


def _reject_float(obj: Any) -> None:
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in an action record")
    if isinstance(obj, Mapping):
        for value in obj.values():
            _reject_float(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _reject_float(value)


def canonical_json(obj: Any) -> bytes:
    """One byte-exact JSON form: sorted keys, no whitespace, no NaN, no float
    anywhere. The same record always encodes to the same bytes."""
    _reject_float(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def record_as_dict(record: ActionRecord) -> dict[str, Any]:
    return {
        "tool": record.tool,
        "arguments": dict(record.arguments),
        "destination": record.destination,
        "amount_minor": record.amount_minor,
        "currency": record.currency,
        "resource": record.resource,
        "resource_version": record.resource_version,
        "proposer": record.proposer,
        "policy_version": record.policy_version,
        "scope": record.scope,
    }


def record_hash(record: ActionRecord) -> str:
    return sha256(canonical_json(record_as_dict(record))).hexdigest()
