"""ActionRecord: two records differing in exactly one field hash differently, for
every field; a record containing a float is refused (spec 1.6, 1.7; acceptance test 6).
"""

from __future__ import annotations

import dataclasses

import pytest

from petasos.trust.record import ActionRecord, canonical_json, record_hash


def _base_record() -> ActionRecord:
    return ActionRecord(
        tool="issue_refund",
        arguments={"ticket": 42, "amount": "42.00", "currency": "USD"},
        destination="customer-42",
        amount_minor=4200,
        currency="USD",
        resource="ticket:42",
        resource_version=3,
        proposer="agent",
        policy_version="1",
        scope="session-1",
    )


_FIELD_VARIANTS = {
    "tool": "delete_ticket",
    "arguments": {"ticket": 43, "amount": "42.00", "currency": "USD"},
    "destination": "customer-43",
    "amount_minor": 4300,
    "resource": "ticket:43",
    "resource_version": 4,
    "proposer": "owner",
    "policy_version": "2",
    "scope": "session-2",
}


@pytest.mark.parametrize("field", sorted(_FIELD_VARIANTS))
def test_one_field_difference_changes_the_hash(field: str) -> None:
    base = _base_record()
    changed = dataclasses.replace(base, **{field: _FIELD_VARIANTS[field]})
    assert record_hash(base) != record_hash(changed)


def test_currency_difference_changes_the_hash() -> None:
    base = _base_record()
    changed = dataclasses.replace(base, currency="EUR", amount_minor=base.amount_minor)
    assert record_hash(base) != record_hash(changed)


def test_amount_and_currency_must_be_set_together() -> None:
    base = _base_record()
    with pytest.raises(ValueError):
        dataclasses.replace(base, amount_minor=None)
    with pytest.raises(ValueError):
        dataclasses.replace(base, currency=None)


def test_record_with_a_float_in_arguments_is_refused() -> None:
    record = dataclasses.replace(_base_record(), arguments={"ticket": 42, "amount": 42.0})
    with pytest.raises(TypeError):
        record_hash(record)


def test_canonical_json_refuses_a_bare_float() -> None:
    with pytest.raises(TypeError):
        canonical_json({"amount": 1.5})


def test_canonical_json_is_byte_stable() -> None:
    obj = {"b": 1, "a": 2}
    assert canonical_json(obj) == canonical_json({"a": 2, "b": 1})
    assert canonical_json(obj) == b'{"a":2,"b":1}'
