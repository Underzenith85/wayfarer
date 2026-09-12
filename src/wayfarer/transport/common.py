"""What every campaign API module shares: bearer identity, JSON bodies, app keys.

The API modules install onto one application and read each other's state out of
it.  Keeping the shared keys and the two request helpers here means an API
module never has to import a sibling just to name them, so the install order is
free to run one way.
"""

from __future__ import annotations

import hmac

from aiohttp import web

from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.errors import AuthenticationError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.providers import Orchestrator

MAX_BODY = 32_000

ACCESS_KEY = web.AppKey("campaign-access", CampaignAccess)
ORCHESTRATOR_KEY = web.AppKey("campaign-orchestrator", Orchestrator)
TOKENS_KEY = web.AppKey("campaign-tokens", dict[str, str])
TEMPLATES_KEY = web.AppKey("setup-templates", tuple[ScenarioGraph, ...])


def _identity(request: web.Request) -> str:
    value = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not value.startswith(prefix):
        raise AuthenticationError("Bearer authentication required")
    supplied = value[len(prefix) :]
    identity = next(
        (
            principal
            for token, principal in request.app[TOKENS_KEY].items()
            if hmac.compare_digest(token, supplied)
        ),
        None,
    )
    if identity is None:
        raise AuthenticationError("Invalid bearer credential")
    return identity


async def _json(request: web.Request) -> dict[str, object]:
    if request.content_type != "application/json" or (
        request.content_length is not None and request.content_length > MAX_BODY
    ):
        raise ValidationError("Invalid JSON request")
    value = await request.json()
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValidationError("JSON object required")
    return value
