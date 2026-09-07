"""Bearer-authenticated HTTP facade for the authoritative play service."""

from __future__ import annotations

import hmac
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path

from aiohttp import web
from pydantic import Field

from wayfarer.config import Settings
from wayfarer.errors import AuthenticationError, ValidationError, WayfarerError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.codex import ProviderStatus
from wayfarer.orchestration.provider_runtime import provider_runtime
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.simulation.resources import Record

ORCHESTRATOR_KEY = web.AppKey("campaign-orchestrator", Orchestrator)
PROVIDER_STATUS_KEY = web.AppKey("provider-status", deque[ProviderStatus])

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
    if request.path.startswith("/api/v1"):
        return await handler(request)
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


class InterpretRequest(Record):
    actor_id: str = Field(min_length=1, max_length=100)
    command_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)


async def interpret(request: web.Request) -> web.Response:
    body = InterpretRequest.model_validate(await _json(request))
    result = await request.app[ORCHESTRATOR_KEY].interpret_and_execute(
        request.match_info["cid"],
        principal_id=_identity(request),
        actor_id=body.actor_id,
        command_id=body.command_id,
        text=body.text,
    )
    return web.json_response(result.model_dump(mode="json"))


async def provider_status(request: web.Request) -> web.Response:
    _, session, _ = await request.app[ORCHESTRATOR_KEY].context(
        request.match_info["cid"], _identity(request), request.query.get("actor_id", "")
    )
    return web.json_response(
        {
            "events": [
                s.model_dump(mode="json", exclude={"session_id"})
                for s in request.app[PROVIDER_STATUS_KEY]
                if s.session_id == session
            ]
        }
    )


def create_campaign_app(
    play: CampaignAccess,
    tokens: Mapping[str, str],
    *,
    settings: Settings | None = None,
    v1_ledger_path: Path | None = None,
    v1_origins: frozenset[str] = frozenset(),
    v1_allow_no_origin: bool = False,
    legacy_routes: bool = False,
) -> web.Application:
    if not tokens or any(not token or not principal for token, principal in tokens.items()):
        raise ValueError("Non-empty credentials required")
    app = web.Application(middlewares=[boundary], client_max_size=MAX_BODY)
    app[ACCESS_KEY] = play
    app[TOKENS_KEY] = dict(tokens)
    app[LIMITS_KEY] = {}
    app.router.add_get("/health", health)
    if legacy_routes:
        app.add_routes(
            [
                web.get("/campaigns/{cid}", read_campaign),
                web.post("/campaigns/{cid}/commands", command),
                web.get("/campaigns/{cid}/events", events),
            ]
        )
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
    from wayfarer.transport.v1.http import TOKENS, install
    from wayfarer.transport.v1.live import CONNECTIONS

    if v1_ledger_path is None:
        if not isinstance(play.play.store, AsyncSQLiteStore):
            raise ValueError("Configure v1_ledger_path for the durable API receipt database")
        v1_ledger_path = play.play.store.path.with_suffix(".v1.sqlite3")
    v1 = install(
        app,
        play.play,
        tokens,
        v1_ledger_path,
        origins=v1_origins,
        allow_no_origin=v1_allow_no_origin,
    )
    app[TOKENS] = app[TOKENS_KEY]
    app[CONNECTIONS] = {}

    async def v1_lifespan(application: web.Application) -> AsyncIterator[None]:
        await v1.start()
        yield
        await v1.close()

    if settings is not None:
        app[PROVIDER_STATUS_KEY] = deque(maxlen=256)

        async def lifespan(application: web.Application) -> AsyncIterator[None]:
            async with provider_runtime(
                settings, status=application[PROVIDER_STATUS_KEY].append
            ) as provider:
                application[ORCHESTRATOR_KEY] = Orchestrator(
                    play, provider, timeout=min(settings.model_timeout_seconds, 120.0), attempts=1
                )
                from wayfarer.transport.v1.provider import bind_provider

                bind_provider(v1, application[ORCHESTRATOR_KEY])
                yield

        app.cleanup_ctx.append(lifespan)
        if legacy_routes:
            app.add_routes(
                [
                    web.post("/campaigns/{cid}/interpret", interpret),
                    web.get("/campaigns/{cid}/provider-status", provider_status),
                ]
            )
    app.cleanup_ctx.append(v1_lifespan)
    return app
