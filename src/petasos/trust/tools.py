"""ToolDefinition, ToolRegistry: server-owned; risk lives here, never with the caller.

A caller names a tool and passes arguments. It never supplies a risk profile, a tier,
a verb, or an approver: those come only from the definition the server registered.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from petasos.trust.risk import RiskProfile

if TYPE_CHECKING:
    from petasos.trust.record import ActionRecord


class InvalidArguments(Exception):
    pass


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class Resolved:
    destination: str | None
    amount_minor: int | None
    currency: str | None
    resource: str
    resource_version: int


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    profile: RiskProfile
    validate: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    resolve: Callable[[sqlite3.Connection, Mapping[str, Any]], Resolved]
    effect: Callable[[sqlite3.Connection, ActionRecord, str], str]
    current_version: Callable[[sqlite3.Connection, str], int]


class ToolRegistry:
    def __init__(self, definitions: list[ToolDefinition]) -> None:
        by_name: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name in by_name:
                raise ValueError(f"duplicate tool name: {definition.name}")
            by_name[definition.name] = definition
        self._by_name = by_name

    def get(self, name: str) -> ToolDefinition | None:
        return self._by_name.get(name)
