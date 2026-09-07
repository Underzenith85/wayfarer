"""Authenticated setup endpoints, independent of provider availability."""

import json

from aiohttp import web

from wayfarer.errors import ValidationError
from wayfarer.orchestration.profiles import ProfileMigrations
from wayfarer.orchestration.setup import SetupService
from wayfarer.simulation.profiles import ProfileSelection
from wayfarer.simulation.setup import CreateSetup, SetupCommand
from wayfarer.simulation.studio import ScenarioGraph

SETUP_KEY = web.AppKey("setup-service", SetupService)
MIGRATIONS_KEY = web.AppKey("profile-migrations", ProfileMigrations)
LEGACY_KEY = web.AppKey("setup-legacy", bool)
TEMPLATES_KEY = web.AppKey("setup-templates", tuple[ScenarioGraph, ...])


async def session(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY, _identity

    return web.json_response(
        {
            "principal_id": _identity(request),
            "generation_available": ORCHESTRATOR_KEY in request.app,
            "legacy_available": request.app.get(LEGACY_KEY, False),
        }
    )


async def listing(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import _identity

    return web.json_response(await request.app[SETUP_KEY].listing(principal_id=_identity(request)))


async def create(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import _identity, _json

    result = await request.app[SETUP_KEY].create(
        CreateSetup.model_validate_json(json.dumps(await _json(request))),
        principal_id=_identity(request),
    )
    return web.json_response(result, status=201)


async def read(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import _identity

    return web.json_response(
        await request.app[SETUP_KEY].read(
            request.match_info["cid"], principal_id=_identity(request)
        )
    )


async def execute(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import _identity, _json

    result = await request.app[SETUP_KEY].execute(
        request.match_info["cid"],
        SetupCommand.model_validate_json(json.dumps(await _json(request))),
        principal_id=_identity(request),
    )
    return web.json_response(result)


async def templates(request: web.Request) -> web.Response:
    # Templates are public authoring material, never another campaign's private graph.
    return web.json_response([g.model_dump(mode="json") for g in request.app[TEMPLATES_KEY]])


async def profiles(request: web.Request) -> web.Response:
    """Registered rules profiles; unsupported ones list their unverified capabilities."""
    from wayfarer.transport.campaign_api import _identity

    _identity(request)
    runtime = request.app[SETUP_KEY].profiles
    views = runtime.views() if runtime is not None else ()
    return web.json_response([v.model_dump(mode="json") for v in views])


def _migrations(request: web.Request) -> ProfileMigrations:
    if MIGRATIONS_KEY not in request.app:
        raise ValidationError("Rules profile migration is unavailable on this server")
    return request.app[MIGRATIONS_KEY]


async def migration_preview(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import _identity

    query = request.query
    if "profile_id" not in query or "version" not in query:
        raise ValidationError("Select a target profile_id and version")
    try:
        chosen = ProfileSelection.model_validate(
            {"id": query["profile_id"], "version": int(query["version"])}
        )
    except ValueError as exc:
        raise ValidationError("Invalid profile selection") from exc
    preview = await _migrations(request).preview(
        request.match_info["cid"], chosen, principal_id=_identity(request)
    )
    return web.json_response({**preview.model_dump(mode="json"), "applicable": preview.applicable})


async def migrate(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import _identity, _json

    principal = _identity(request)
    entry = await _migrations(request).apply(
        request.match_info["cid"], await _json(request), principal_id=principal
    )
    setup = await request.app[SETUP_KEY].read(request.match_info["cid"], principal_id=principal)
    return web.json_response({"entry": entry.model_dump(mode="json"), "setup": setup})


def install(app: web.Application, service: SetupService, graphs: tuple[ScenarioGraph, ...]) -> None:
    app[SETUP_KEY] = service
    app[TEMPLATES_KEY] = graphs
    from wayfarer.orchestration.catalog import ScenarioCatalog
    from wayfarer.transport.campaign_api import TOKENS_KEY
    from wayfarer.transport.catalog_api import install as install_catalog

    install_catalog(app, ScenarioCatalog(service, frozenset(app[TOKENS_KEY].values())))
    if service.profiles is not None:
        app[MIGRATIONS_KEY] = ProfileMigrations(service.profiles)
    app.add_routes(
        [
            web.get("/setups", listing),
            web.get("/setups/session", session),
            web.post("/setups", create),
            web.get("/setups/templates", templates),
            web.get("/setups/profiles", profiles),
            web.get("/setups/{cid}", read),
            web.post("/setups/{cid}", execute),
            web.get("/setups/{cid}/migration", migration_preview),
            web.post("/setups/{cid}/migration", migrate),
        ]
    )


async def generate(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY, _identity, _json

    result = await request.app[SETUP_KEY].generate(
        request.match_info["cid"],
        SetupCommand.model_validate_json(json.dumps(await _json(request))),
        request.app[ORCHESTRATOR_KEY],
        principal_id=_identity(request),
    )
    return web.json_response(result)
