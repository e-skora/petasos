"""The guard: `scan` and `assert_no_private_payload` (spec 002-memory-canary-guard,
2.11 to 2.13). Destination-blind: neither function takes a destination, an identity,
a route, or a ceiling. A broken guard aborts rather than serving the response, so
every bound violation, unsupported type, or exception raised while scanning becomes a
`guard_error` hit instead of propagating.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection
from typing import Literal

from petasos.memory.canary import CanarySet, canary_set

MAX_NODES = 10_000
MAX_TEXT_BYTES = 2_097_152
MAX_DEPTH = 64

_SENTENCES = {
    "canary": "Private memory was about to leave the system, so the response was stopped.",
    "private_key": (
        "A field marked private was about to leave the system, so the response was stopped."
    ),
    "guard_error": "The response could not be checked for private data, so it was stopped.",
}


@dataclasses.dataclass(frozen=True)
class Hit:
    kind: Literal["canary", "private_key", "guard_error"]
    token: str | None
    key: str | None
    detail: str


class GuardTripped(Exception):
    def __init__(self, hit: Hit) -> None:
        super().__init__(hit.detail)
        self.hit = hit


class _BoundExceeded(Exception):
    pass


class _Counters:
    __slots__ = ("nodes", "text_bytes")

    def __init__(self) -> None:
        self.nodes = 0
        self.text_bytes = 0


def _count_node(counters: _Counters) -> None:
    counters.nodes += 1
    if counters.nodes > MAX_NODES:
        raise _BoundExceeded("too many nodes")


def _count_text(counters: _Counters, nbytes: int) -> None:
    counters.text_bytes += nbytes
    if counters.text_bytes > MAX_TEXT_BYTES:
        raise _BoundExceeded("too much scanned text")


def _scan_text(text: str, canaries: CanarySet) -> Hit | None:
    start = 0
    while True:
        idx = text.find("cn-", start)
        if idx == -1:
            return None
        window = text[idx : idx + 25]
        if len(window) == 25 and window in canaries:
            return Hit(kind="canary", token=window, key=None, detail=_SENTENCES["canary"])
        start = idx + 1


def _is_private_key(key: str) -> bool:
    return key == "private" or key.startswith("private_")


def _walk(obj: object, canaries: CanarySet, counters: _Counters, depth: int) -> Hit | None:
    if isinstance(obj, (dict, list, tuple)):
        if depth > MAX_DEPTH:
            raise _BoundExceeded("nested too deep")
        _count_node(counters)
        if isinstance(obj, dict):
            for key, value in obj.items():
                _count_node(counters)
                if isinstance(key, str):
                    _count_text(counters, len(key.encode("utf-8")))
                    hit = _scan_text(key, canaries)
                    if hit is not None:
                        return hit
                    if _is_private_key(key):
                        return Hit(
                            kind="private_key",
                            token=None,
                            key=key,
                            detail=_SENTENCES["private_key"],
                        )
                elif isinstance(key, bytes):
                    _count_text(counters, len(key))
                    hit = _scan_text(key.decode("utf-8", errors="replace"), canaries)
                    if hit is not None:
                        return hit
                hit = _walk(value, canaries, counters, depth + 1)
                if hit is not None:
                    return hit
            return None
        for item in obj:
            hit = _walk(item, canaries, counters, depth + 1)
            if hit is not None:
                return hit
        return None
    if isinstance(obj, str):
        _count_node(counters)
        _count_text(counters, len(obj.encode("utf-8")))
        return _scan_text(obj, canaries)
    if isinstance(obj, bytes):
        _count_node(counters)
        _count_text(counters, len(obj))
        return _scan_text(obj.decode("utf-8", errors="replace"), canaries)
    if isinstance(obj, (int, float, bool)) or obj is None:
        _count_node(counters)
        return None
    raise _BoundExceeded(f"unsupported payload type: {type(obj).__name__}")


def scan(payload: object, canaries: Collection[str]) -> Hit | None:
    try:
        registry = canaries if isinstance(canaries, CanarySet) else canary_set(canaries)
        return _walk(payload, registry, _Counters(), 1)
    except Exception:  # noqa: BLE001 - a broken guard aborts rather than serving the response
        return Hit(kind="guard_error", token=None, key=None, detail=_SENTENCES["guard_error"])


def assert_no_private_payload(payload: object, canaries: Collection[str]) -> None:
    hit = scan(payload, canaries)
    if hit is not None:
        raise GuardTripped(hit)
