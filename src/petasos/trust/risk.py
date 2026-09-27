"""RiskProfile (no tier field), derive_tier, verb_for, rail_for, POLICY_VERSION.

A tool definition carries a `RiskProfile`, the risk facts a caller can never supply.
`derive_tier` turns those facts into a tier by fixed rule, never by declaration.
Changing this table, the verb table, or a hard constraint (`constraints.py`) bumps
`POLICY_VERSION`, so an approval made under one rulebook never executes under another.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

POLICY_VERSION = "1"


class InvalidProfile(Exception):
    pass


class Tier(Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"


_TIER_ORDER = {Tier.L1: 1, Tier.L2: 2, Tier.L3: 3, Tier.L4: 4, Tier.L5: 5}


def tier_at_least(candidate: Tier, floor: Tier) -> bool:
    return _TIER_ORDER[candidate] >= _TIER_ORDER[floor]


@dataclass(frozen=True)
class RiskProfile:
    mutation: Literal["none", "internal", "external"]
    reversible: bool
    touches_money: bool
    deletes: bool

    def __post_init__(self) -> None:
        if self.mutation == "none" and (self.touches_money or self.deletes or not self.reversible):
            raise InvalidProfile(
                "mutation='none' only ever pairs with reversible=True, "
                "touches_money=False, deletes=False"
            )


def derive_tier(profile: RiskProfile) -> Tier:
    if profile.mutation == "none":
        floor = Tier.L1
    elif profile.mutation == "internal":
        floor = Tier.L2 if profile.reversible else Tier.L3
    else:
        floor = Tier.L4
    if profile.touches_money or profile.deletes:
        return Tier.L5
    return floor


def verb_for(profile: RiskProfile) -> str:
    if profile.touches_money:
        return "ISSUE-REFUND"
    if profile.deletes:
        return "DELETE-TICKET"
    if profile.mutation == "external":
        return "SEND-EMAIL"
    if profile.mutation == "internal":
        return "CHANGE-RECORD" if not profile.reversible else "ADD-NOTE"
    return "READ"


_RAILS: dict[str, tuple[str, int]] = {
    "ISSUE-REFUND": ("money", 2),
    "DELETE-TICKET": ("destructive", 2),
    "SEND-EMAIL": ("external", 5),
    "CHANGE-RECORD": ("internal", 30),
    "ADD-NOTE": ("internal", 30),
}


def rail_for(verb: str) -> tuple[str, int] | None:
    return _RAILS.get(verb)
