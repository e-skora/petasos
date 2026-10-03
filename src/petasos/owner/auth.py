"""Authentication for `/owner/*`: a bearer string to an `Identity`, the same parsing
`IdentityMiddleware` uses for `/mcp` (spec 4.1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from starlette.requests import Request

from petasos.mcp.identity import ClientRegistry, Identity


class Unauthenticated(Exception):
    pass


def identity_for(
    request: Request, *, registry: ClientRegistry, clock: Callable[[], datetime]
) -> Identity:
    header_value = request.headers.get("authorization")
    if header_value is None:
        raise Unauthenticated()
    scheme, _, token = header_value.partition(" ")
    if scheme != "Bearer" or not token:
        raise Unauthenticated()
    identity = registry.authenticate(token, now=clock())
    if identity is None:
        raise Unauthenticated()
    request.state.identity = identity
    return identity
