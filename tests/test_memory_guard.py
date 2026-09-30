"""The guard: destination-blind scanning, its bounds, and its fixed sentences (spec
002-memory-canary-guard 2.11 to 2.13; acceptance tests 17 to 24).
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime

import pytest

from petasos.memory import GuardTripped, assert_no_private_payload, canary_set, mint_canary, scan
from petasos.memory.guard import MAX_DEPTH, MAX_NODES, MAX_TEXT_BYTES


def _tokens(n: int) -> list[str]:
    return [mint_canary() for _ in range(n)]


def test_scan_and_assert_take_exactly_payload_and_canaries() -> None:
    for fn in (scan, assert_no_private_payload):
        params = list(inspect.signature(fn).parameters)
        assert params == ["payload", "canaries"]
        for name in params:
            for word in ("destination", "identity", "client", "route", "ceiling"):
                assert word not in name


def test_canary_hit_in_various_positions() -> None:
    token = mint_canary()
    registry = canary_set([token])

    payloads = [
        {"outer": {"inner": token}},
        {"items": ["a", token, "b"]},
        {token: "value"},
        {"note": f"here is the token: {token} embedded in a sentence."},
        {"raw": token.encode("utf-8")},
    ]
    for payload in payloads:
        hit = scan(payload, registry)
        assert hit is not None
        assert hit.kind == "canary"
        assert hit.token == token

    tampered = token[:-1] + ("x" if token[-1] != "x" else "y")
    assert scan({"note": tampered}, registry) is None


@pytest.mark.parametrize(
    ("key", "expect_hit"),
    [
        ("private", True),
        ("private_notes", True),
        ("private_x", True),
        ("Private_notes", False),
        ("notes_private", False),
        ("privately", False),
    ],
)
def test_private_labelled_keys(key: str, expect_hit: bool) -> None:
    registry = canary_set([])
    hit = scan({key: ""}, registry)
    if expect_hit:
        assert hit is not None
        assert hit.kind == "private_key"
        assert hit.key == key
    else:
        assert hit is None


def test_assert_no_private_payload_raises_with_the_hit_and_a_plain_sentence() -> None:
    registry = canary_set([])
    with pytest.raises(GuardTripped) as excinfo:
        assert_no_private_payload({"private_note": ""}, registry)
    hit = excinfo.value.hit
    assert hit.kind == "private_key"
    assert str(excinfo.value) == hit.detail
    assert "private_note" not in str(excinfo.value)


def test_unsupported_type_deep_nesting_and_broken_iteration_are_guard_errors() -> None:
    registry = canary_set([])

    for bad in ({1, 2, 3}, datetime.now(UTC), object()):
        hit = scan(bad, registry)
        assert hit is not None and hit.kind == "guard_error"

    nested = 0
    for _ in range(65):
        nested = [nested]
    hit = scan(nested, registry)
    assert hit is not None and hit.kind == "guard_error"

    class BadDict(dict):
        def items(self):
            raise RuntimeError("boom")

    hit = scan(BadDict(a=1), registry)
    assert hit is not None and hit.kind == "guard_error"

    assert scan("safe payload", registry) is None


def test_node_bound_at_and_over_the_limit() -> None:
    registry = canary_set([])

    assert scan(tuple(range(MAX_NODES - 1)), registry) is None
    assert scan(tuple(range(MAX_NODES)), registry).kind == "guard_error"

    assert scan([{} for _ in range(MAX_NODES - 1)], registry) is None
    assert scan([{} for _ in range(MAX_NODES)], registry).kind == "guard_error"

    assert scan([{"k": "v"} for _ in range(3333)], registry) is None
    assert scan([{"k": "v"} for _ in range(3334)], registry).kind == "guard_error"


def test_depth_bound_at_and_over_the_limit() -> None:
    registry = canary_set([])

    clean = 0
    for _ in range(MAX_DEPTH):
        clean = [clean]
    assert scan(clean, registry) is None

    over = 0
    for _ in range(MAX_DEPTH + 1):
        over = [over]
    assert scan(over, registry).kind == "guard_error"


def test_text_byte_bound_at_and_over_the_limit_for_str_and_bytes() -> None:
    registry = canary_set([])

    assert scan("x" * MAX_TEXT_BYTES, registry) is None
    assert scan("x" * (MAX_TEXT_BYTES + 1), registry).kind == "guard_error"

    assert scan(b"x" * MAX_TEXT_BYTES, registry) is None
    assert scan(b"x" * (MAX_TEXT_BYTES + 1), registry).kind == "guard_error"

    chunk = "x" * 1024
    n_clean = MAX_TEXT_BYTES // len(chunk)
    assert scan([chunk] * n_clean, registry) is None
    assert scan([chunk] * (n_clean + 1), registry).kind == "guard_error"


def test_large_registry_scans_fast_and_still_catches_an_old_token() -> None:
    registry = canary_set(_tokens(100_000))
    old_token = next(iter(registry))
    payload = {"body": "clean text " * 100_000}

    start = time.perf_counter()
    hit = scan(payload, registry)
    assert time.perf_counter() - start < 1.0
    assert hit is None

    planted = {"body": f"leaked: {old_token}"}
    assert scan(planted, registry).token == old_token

    malformed = list(registry)[:5] + ["cn-short"]
    hit = scan({"body": "clean"}, malformed)
    assert hit.kind == "guard_error"
    with pytest.raises(ValueError):
        canary_set(malformed)


def test_a_canaryset_is_used_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = canary_set(_tokens(10))
    calls = []
    import petasos.memory.guard as guard_module

    monkeypatch.setattr(guard_module, "canary_set", lambda tokens: calls.append(tokens))
    assert scan({"body": "clean"}, registry) is None
    assert calls == []


def test_1mb_payload_with_1000_canaries_scans_fast() -> None:
    registry = canary_set(_tokens(1000))
    payload = {"body": "clean text " * 100_000}
    start = time.perf_counter()
    hit = scan(payload, registry)
    assert time.perf_counter() - start < 1.0
    assert hit is None
