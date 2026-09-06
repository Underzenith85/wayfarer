"""Bearer-authenticated HTTP facade for the authoritative play service."""

from __future__ import annotations

import hmac
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping

from aiohttp import web

from wayfarer.errors import AuthenticationError, ValidationError, WayfarerError
from wayfarer.orchestration.access import CampaignAccess

MAX_BODY = 32_000
ACCESS_KEY = web.AppKey("campaign-access", CampaignAccess)
TOKENS_KEY = web.AppKey("campaign-tokens", dict[str, str])
LIMITS_KEY = web.AppKey("campaign-limits", dict[str, tuple[float, int]])


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


@web.middleware
async def boundary(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    try:
        if request.path != "/health":
            identity = _identity(request)
            now = time.monotonic()
            start, count = request.app[LIMITS_KEY].get(identity, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            if count >= 120:
                return web.json_response(
                    {"error": "Rate limit exceeded", "code": "rate_limit"}, status=429
                )
            request.app[LIMITS_KEY][identity] = (start, count + 1)
        response = await handler(request)
    except WayfarerError as exc:
        response = web.json_response({"error": str(exc), "code": exc.code}, status=exc.status)
    except (ValueError, TypeError, KeyError):
        response = web.json_response(
            {"error": "Invalid request", "code": "validation_error"}, status=400
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    return response


async def _json(request: web.Request) -> dict[str, object]:
    if request.content_type != "application/json" or (
        request.content_length is not None and request.content_length > MAX_BODY
    ):
        raise ValidationError("Invalid JSON request")
    value = await request.json()
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValidationError("JSON object required")
    return value


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def read_campaign(request: web.Request) -> web.Response:
    result = await request.app[ACCESS_KEY].read(
        request.match_info["cid"], principal_id=_identity(request)
    )
    return web.json_response(result)


async def command(request: web.Request) -> web.Response:
    result = await request.app[ACCESS_KEY].execute(
        request.match_info["cid"], await _json(request), principal_id=_identity(request)
    )
    return web.json_response(result)


async def events(request: web.Request) -> web.Response:
    after = int(request.query.get("after", "0"))
    values = await request.app[ACCESS_KEY].events(
        request.match_info["cid"], principal_id=_identity(request), after=after
    )
    return web.json_response({"events": [value.model_dump(mode="json") for value in values]})


def create_campaign_app(play: CampaignAccess, tokens: Mapping[str, str]) -> web.Application:
    if not tokens or any(not token or not principal for token, principal in tokens.items()):
        raise ValueError("Non-empty credentials required")
    app = web.Application(middlewares=[boundary], client_max_size=MAX_BODY)
    app[ACCESS_KEY] = play
    app[TOKENS_KEY] = dict(tokens)
    app[LIMITS_KEY] = {}
    app.add_routes(
        [
            web.get("/health", health),
            web.get("/campaigns/{cid}", read_campaign),
            web.post("/campaigns/{cid}/commands", command),
            web.get("/campaigns/{cid}/events", events),
        ]
    )
    return app
