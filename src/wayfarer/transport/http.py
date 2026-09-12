"""Async HTTP transport with correlation IDs and typed error mapping."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.resources import files

import aiohttp
import structlog
from aiohttp import web

from wayfarer import validation
from wayfarer.config import Settings
from wayfarer.engine.character import builder
from wayfarer.engine.rules import catalog
from wayfarer.engine.simulation.scenario import scenario, validate_scenario
from wayfarer.errors import ValidationError, WayfarerError
from wayfarer.orchestration.llm import CHARACTER_SCHEMA, SCENARIO_SCHEMA, LLMClient
from wayfarer.orchestration.service import GameService, public

log = structlog.get_logger()
ROOT = files("wayfarer.transport").joinpath("static")
MAX_BODY = 32_000
SERVICE_KEY = web.AppKey("service", GameService)


def json_response(data: object, status: int = 200) -> web.Response:
    return web.json_response(
        data,
        status=status,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@web.middleware
async def errors(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    try:
        response = await handler(request)
    except WayfarerError as exc:
        await log.awarning(
            "request_failed", correlation_id=correlation_id, error_code=exc.code, path=request.path
        )
        response = json_response({"error": str(exc), "code": exc.code}, exc.status)
    except (ValueError, KeyError, TypeError) as exc:
        response = json_response({"error": str(exc), "code": "validation_error"}, 400)
    except Exception:
        await log.aexception("request_crashed", correlation_id=correlation_id, path=request.path)
        response = json_response({"error": "Internal server error", "code": "internal_error"}, 500)
    response.headers["X-Request-ID"] = correlation_id
    return response


async def body(request: web.Request) -> dict[str, object]:
    if request.content_type != "application/json":
        raise ValidationError("JSON required")
    if request.content_length is not None and request.content_length > MAX_BODY:
        raise ValidationError("Invalid request size")
    return validation.mapping(await request.json())


async def bootstrap(request: web.Request) -> web.Response:
    service: GameService = request.app[SERVICE_KEY]
    return json_response(
        {
            "mode": "LLM connected" if service.llm.enabled else "Offline demo",
            "character": builder.character(),
            "scenario": scenario(),
            "campaigns": await service.listing(),
            "rules": {
                "version": catalog.VERSION,
                "budget": catalog.BUDGET,
                "traits": dict(catalog.TRAITS),
            },
        }
    )


async def get_campaign(request: web.Request) -> web.Response:
    service: GameService = request.app[SERVICE_KEY]
    return json_response(public(await service.read(request.match_info["cid"])))


async def create_campaign(request: web.Request) -> web.Response:
    data = await body(request)
    service: GameService = request.app[SERVICE_KEY]
    return json_response(await service.create(data.get("character"), data.get("scenario")), 201)


async def turn(request: web.Request) -> web.Response:
    """The retired prototype endpoint is outside the frozen /api/v1 contract."""
    return json_response(
        {"code": "prototype_retired", "message": "Use the typed campaign actions API."}, 410
    )


async def validate_character(request: web.Request) -> web.Response:
    return json_response(builder.validate((await body(request)).get("character")))


async def generate_character(request: web.Request) -> web.Response:
    data = await body(request)
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2000:
        raise ValidationError("Describe your idea in 1–2000 characters")
    service: GameService = request.app[SERVICE_KEY]
    if service.llm.enabled:
        raw = await service.llm.generate(
            "Create a legal character using only the supplied closed ruleset.",
            {
                "concept": prompt,
                "current": data.get("current"),
                "traits": dict(catalog.TRAITS),
                "skills": dict(catalog.SKILLS),
            },
            CHARACTER_SCHEMA,
        )
        draft = validation.character(raw)
    else:
        draft = builder.character()
        draft["concept"] = prompt[:1000]
    return json_response(
        {
            "draft": draft,
            "validation": builder.validate(draft),
            "mode": "LLM" if service.llm.enabled else "Preset demo; concept text updated",
        }
    )


async def generate_scenario(request: web.Request) -> web.Response:
    data = await body(request)
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2000:
        raise ValidationError("Describe your idea in 1–2000 characters")
    service: GameService = request.app[SERVICE_KEY]
    if service.llm.enabled:
        raw = await service.llm.generate(
            "Write a scenario skin for the fixed dockside mystery topology.",
            {"prompt": prompt},
            SCENARIO_SCHEMA,
        )
        draft = validation.scenario(raw)
    else:
        draft = scenario()
        draft["premise"] += " Adventure brief: " + prompt[:1000]
    validate_scenario(draft)
    return json_response(
        {"draft": draft, "mode": "LLM" if service.llm.enabled else "Preset demo; brief appended"}
    )


async def static(request: web.Request) -> web.Response:
    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}.get(request.path)
    if name is None:
        raise web.HTTPNotFound()
    mime = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[name.rsplit(".", 1)[1]]
    return web.Response(
        body=ROOT.joinpath(name).read_bytes(),
        content_type=mime,
        headers={"Cache-Control": "no-store"},
    )


def create_app(settings: Settings) -> web.Application:
    app = web.Application(middlewares=[errors], client_max_size=MAX_BODY)

    async def context(application: web.Application) -> AsyncIterator[None]:
        async with aiohttp.ClientSession() as session:
            application[SERVICE_KEY] = GameService(settings, LLMClient(settings, session))
            yield

    app.cleanup_ctx.append(context)
    app.add_routes(
        [
            web.get("/api/bootstrap", bootstrap),
            web.get("/api/campaigns/{cid}", get_campaign),
            web.post("/api/campaigns", create_campaign),
            web.post("/api/campaigns/{cid}/turn", turn),
            web.post("/api/validate", validate_character),
            web.post("/api/generate/character", generate_character),
            web.post("/api/generate/scenario", generate_scenario),
            web.get("/", static),
            web.get("/app.js", static),
            web.get("/style.css", static),
        ]
    )
    return app
