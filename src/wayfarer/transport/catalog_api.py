"""Scenario authoring API v1: additive to, and independent of, frozen play v1."""

from aiohttp import web

from wayfarer.errors import ValidationError
from wayfarer.orchestration.catalog import ScenarioCatalog
from wayfarer.orchestration.scenario_documents import adapt_graph
from wayfarer.simulation.catalog import CatalogCommand, InstantiateRevision
from wayfarer.simulation.scenario_document import PublicBrief
from wayfarer.transport.campaign_api import _identity
from wayfarer.transport.setup_api import TEMPLATES_KEY

KEY = web.AppKey("scenario-catalog", ScenarioCatalog)
MAX_BODY = 12_100_000  # Worst-case JSON escaping of the 2 MB document string.


async def body(request: web.Request) -> str:
    if request.content_type != "application/json":
        raise ValidationError("JSON required")
    raw = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise ValidationError("Scenario request exceeds size limit")
    return raw.decode("utf-8")


async def listing(request: web.Request) -> web.Response:
    values = await request.app[KEY].listing(_identity(request))
    return web.json_response([v.model_dump(mode="json") for v in values])


async def templates(request: web.Request) -> web.Response:
    service = request.app[KEY]
    service.documents.authorize(_identity(request))
    return web.json_response(
        [
            adapt_graph(
                g,
                studio=service.documents.studio,
                revision_id=f"{g.id}-bundled",
                author="Wayfarer",
                public=PublicBrief(
                    title=g.title,
                    summary=g.brief.premise,
                    setup=g.brief,
                    opening_prompt=g.opening_action,
                ),
            ).model_dump(mode="json")
            for g in request.app[TEMPLATES_KEY]
        ]
    )


async def read(request: web.Request) -> web.Response:
    revision = int(request.query["revision"]) if "revision" in request.query else None
    value = await request.app[KEY].read(request.match_info["cid"], _identity(request), revision)
    if request.path.endswith("/export"):
        return web.Response(text=value.revision.draft.content_json, content_type="application/json")
    if request.path.endswith("/preview"):
        from wayfarer.orchestration.scenario_documents import ScenarioDocuments

        if value.revision.published is None:
            raise ValidationError("Preview requires a published revision")
        return web.json_response(
            ScenarioDocuments.player_export(value.revision.published).model_dump(mode="json")
        )
    return web.json_response(value.model_dump(mode="json"))


async def execute(request: web.Request) -> web.Response:
    command = CatalogCommand.model_validate_json(await body(request))
    service = request.app[KEY]
    value = await service.execute(_identity(request), command, request.match_info.get("cid"))
    # Return the receipt's version, never a later concurrently modified revision.
    return web.json_response(service.summary(value).model_dump(mode="json"))


async def instantiate(request: web.Request) -> web.Response:
    result = await request.app[KEY].instantiate(
        request.match_info["cid"],
        _identity(request),
        InstantiateRevision.model_validate_json(await body(request)),
    )
    return web.json_response(result, status=201)


def install(app: web.Application, service: ScenarioCatalog) -> None:
    app[KEY] = service
    prefix = "/authoring/v1/scenarios"
    app.add_routes(
        [
            web.get(prefix, listing),
            web.post(prefix, execute),
            web.get(prefix + "/templates", templates),
            web.get(prefix + "/{cid}", read),
            web.post(prefix + "/{cid}", execute),
            web.get(prefix + "/{cid}/export", read),
            web.get(prefix + "/{cid}/preview", read),
            web.post(prefix + "/{cid}/instantiate", instantiate),
        ]
    )
