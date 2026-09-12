"""The v1 application's own keys, and the bearer lookup that reads one.

These name the state http and live both reach for, so they sit below both
rather than in the module that happens to install the routes.
"""

from __future__ import annotations

import hmac

from aiohttp import web

from .common import Fault
from .service import V1Service

SERVICE = web.AppKey("v1-service", V1Service)
TOKENS = web.AppKey("v1-tokens", dict[str, str])
ORIGINS = web.AppKey("v1-origins", frozenset[str])
NO_ORIGIN = web.AppKey("v1-no-origin", bool)


def identity(tokens: dict[str, str], credential: str) -> str:
    for key, principal in tokens.items():
        if hmac.compare_digest(key, credential):
            return principal
    raise Fault(401, "unauthenticated")
