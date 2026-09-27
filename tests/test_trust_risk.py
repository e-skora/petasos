"""RiskProfile has no tier field; derive_tier is pure and monotone; every valid profile
maps to its expected (tier, verb); rail_for covers every verb but READ (spec 1.1, 1.3,
1.4, 1.8, 1.13; acceptance tests 1, 2, 3, 4).
"""

from __future__ import annotations

import dataclasses
import itertools

from hypothesis import given
from hypothesis import strategies as st

from petasos.trust.risk import (
    InvalidProfile,
    RiskProfile,
    Tier,
    derive_tier,
    rail_for,
    tier_at_least,
    verb_for,
)

_MUTATIONS = ("none", "internal", "external")


def _all_combinations():
    yield from itertools.product(_MUTATIONS, (True, False), (True, False), (True, False))


def _is_valid(mutation: str, reversible: bool, touches_money: bool, deletes: bool) -> bool:
    return not (mutation == "none" and (touches_money or deletes or not reversible))


def test_risk_profile_has_no_tier_attribute() -> None:
    fields = {f.name for f in dataclasses.fields(RiskProfile)}
    assert not any("tier" in name for name in fields)


def test_invalid_profiles_raise() -> None:
    for mutation, reversible, touches_money, deletes in _all_combinations():
        if _is_valid(mutation, reversible, touches_money, deletes):
            continue
        try:
            RiskProfile(
                mutation=mutation,
                reversible=reversible,
                touches_money=touches_money,
                deletes=deletes,
            )
        except InvalidProfile:
            continue
        raise AssertionError(
            f"expected InvalidProfile for {mutation, reversible, touches_money, deletes}"
        )


# The expected (tier, verb) pair for every one of the 17 valid profiles (spec 1.3, 1.4).
_EXPECTED = {
    ("none", True, False, False): (Tier.L1, "READ"),
    ("internal", True, False, False): (Tier.L2, "ADD-NOTE"),
    ("internal", False, False, False): (Tier.L3, "CHANGE-RECORD"),
    ("internal", True, True, False): (Tier.L5, "ISSUE-REFUND"),
    ("internal", False, True, False): (Tier.L5, "ISSUE-REFUND"),
    ("internal", True, False, True): (Tier.L5, "DELETE-TICKET"),
    ("internal", False, False, True): (Tier.L5, "DELETE-TICKET"),
    ("internal", True, True, True): (Tier.L5, "ISSUE-REFUND"),
    ("internal", False, True, True): (Tier.L5, "ISSUE-REFUND"),
    ("external", True, False, False): (Tier.L4, "SEND-EMAIL"),
    ("external", False, False, False): (Tier.L4, "SEND-EMAIL"),
    ("external", True, True, False): (Tier.L5, "ISSUE-REFUND"),
    ("external", False, True, False): (Tier.L5, "ISSUE-REFUND"),
    ("external", True, False, True): (Tier.L5, "DELETE-TICKET"),
    ("external", False, False, True): (Tier.L5, "DELETE-TICKET"),
    ("external", True, True, True): (Tier.L5, "ISSUE-REFUND"),
    ("external", False, True, True): (Tier.L5, "ISSUE-REFUND"),
}


def test_all_17_valid_profiles_map_to_expected_tier_and_verb() -> None:
    valid = [c for c in _all_combinations() if _is_valid(*c)]
    assert len(valid) == 17
    assert set(valid) == set(_EXPECTED)
    for mutation, reversible, touches_money, deletes in valid:
        profile = RiskProfile(
            mutation=mutation, reversible=reversible, touches_money=touches_money, deletes=deletes
        )
        expected_tier, expected_verb = _EXPECTED[(mutation, reversible, touches_money, deletes)]
        assert derive_tier(profile) == expected_tier
        assert verb_for(profile) == expected_verb


def test_rail_for_covers_every_verb_but_read() -> None:
    verbs = {verb for (_tier, verb) in _EXPECTED.values()} - {"READ"}
    for verb in verbs:
        assert rail_for(verb) is not None
    assert rail_for("READ") is None


_valid_profiles = st.sampled_from([c for c in _all_combinations() if _is_valid(*c)]).map(
    lambda c: RiskProfile(mutation=c[0], reversible=c[1], touches_money=c[2], deletes=c[3])
)


@given(_valid_profiles)
def test_derive_tier_is_monotone(profile: RiskProfile) -> None:
    base_tier = derive_tier(profile)

    if not profile.touches_money and _is_valid(
        profile.mutation, profile.reversible, True, profile.deletes
    ):
        raised = dataclasses.replace(profile, touches_money=True)
        assert tier_at_least(derive_tier(raised), base_tier)

    if not profile.deletes and _is_valid(
        profile.mutation, profile.reversible, profile.touches_money, True
    ):
        raised = dataclasses.replace(profile, deletes=True)
        assert tier_at_least(derive_tier(raised), base_tier)

    if profile.reversible and _is_valid(
        profile.mutation, False, profile.touches_money, profile.deletes
    ):
        lowered = dataclasses.replace(profile, reversible=False)
        assert tier_at_least(derive_tier(lowered), base_tier)

    if profile.mutation != "external":
        next_mutation = "internal" if profile.mutation == "none" else "external"
        if _is_valid(next_mutation, profile.reversible, profile.touches_money, profile.deletes):
            raised = dataclasses.replace(profile, mutation=next_mutation)
            assert tier_at_least(derive_tier(raised), base_tier)
