"""Scenario authoring API v1: additive to, and independent of, frozen play v1."""

from collections.abc import AsyncIterator

from aiohttp import web
from pydantic import Field

from wayfarer.engine.simulation.campaign.scenario_catalog import (
    CatalogCommand,
    InstantiateRevision,
    ScenarioGenerationRequest,
)
from wayfarer.engine.simulation.campaign.scenario_document import PublicBrief
from wayfarer.errors import (
    ConflictError,
    ProviderError,
    ValidationError,
)
from wayfarer.models import Record
from wayfarer.orchestration.catalog import ScenarioCatalog
from wayfarer.orchestration.scenario_authoring import SCENARIO_AUTHORING, AuthoringWork
from wayfarer.orchestration.scenario_documents import ScenarioDocuments, adapt_graph
from wayfarer.transport.common import ORCHESTRATOR_KEY, TEMPLATES_KEY, _identity

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


async def create_generation(request: web.Request) -> web.Response:

    if ORCHESTRATOR_KEY not in request.app:
        raise ProviderError("Scenario generation is unavailable; choose a template or manual draft")
    principal = _identity(request)
    job = await request.app[KEY].create_generation_job(
        principal, ScenarioGenerationRequest.model_validate_json(await body(request))
    )
    await _author(request.app, principal, job.id)
    return web.json_response(job.model_dump(mode="json"), status=202)


async def read_generation(request: web.Request) -> web.Response:
    principal = _identity(request)
    job = await request.app[KEY].read_generation_job(principal, request.match_info["jid"])
    # A queued/running row whose process is no longer in flight survived a restart.
    # Make that recovery explicit rather than leaving the row looking busy.
    if job.status in ("queued", "running") and await _interrupted(request.app, job.id):
        try:
            job = await request.app[KEY].store.update_job(
                job.model_copy(
                    update={
                        "status": "failed",
                        "error_code": "generation_interrupted",
                        "error_message": "Generation was interrupted. Retry to continue.",
                    }
                ),
                job.version,
            )
        except ConflictError:
            # The live worker won the race after our read; return its newer
            # state instead of leaking an internal optimistic-lock conflict.
            job = await request.app[KEY].read_generation_job(principal, job.id)
    return web.json_response(job.model_dump(mode="json"))


async def cancel_generation(request: web.Request) -> web.Response:
    principal = _identity(request)
    job = await request.app[KEY].cancel_generation_job(principal, request.match_info["jid"])
    return web.json_response(job.model_dump(mode="json"))


class RetryRequest(Record):
    expected_version: int = Field(ge=1)


async def retry_generation(request: web.Request) -> web.Response:

    if ORCHESTRATOR_KEY not in request.app:
        raise ProviderError("Scenario generation is unavailable; saved drafts remain editable")
    principal = _identity(request)
    expected = RetryRequest.model_validate_json(await body(request)).expected_version
    job = await request.app[KEY].read_generation_job(principal, request.match_info["jid"])
    if job.version != expected:
        raise ConflictError("Generation job changed; reload before retrying")
    if job.status not in ("failed", "cancelled"):
        raise ConflictError("Only failed or cancelled generation can be retried")
    job = await request.app[KEY].store.update_job(
        job.model_copy(
            update={
                "status": "queued",
                "proposal_json": None,
                "report": None,
                "error_code": None,
                "error_message": None,
            }
        ),
        job.version,
    )
    await _author(request.app, principal, job.id)
    return web.json_response(job.model_dump(mode="json"), status=202)


async def _author(app: web.Application, principal: str, job_id: str) -> None:
    """Hand the job to the process worker, which owns its bounds and its restart."""
    llm = app[ORCHESTRATOR_KEY]
    await llm.processes.run(
        SCENARIO_AUTHORING.name, AuthoringWork(app[KEY], llm, principal, job_id)
    )


async def _interrupted(app: web.Application, job_id: str) -> bool:
    """Whether this job's process is no longer running anywhere."""
    if ORCHESTRATOR_KEY not in app:
        return True
    processes = app[ORCHESTRATOR_KEY].processes
    try:
        process = await processes.status("scenario-authoring:" + job_id)
    except ConflictError:
        return True
    return process.status in ("failed", "succeeded") and process.id not in processes.tasks


def install(app: web.Application, service: ScenarioCatalog) -> None:
    app[KEY] = service
    prefix = "/authoring/v1/scenarios"
    app.add_routes(
        [
            web.get(prefix, listing),
            web.post(prefix, execute),
            web.get(prefix + "/templates", templates),
            web.post(prefix + "/generation-jobs", create_generation),
            web.get(prefix + "/generation-jobs/{jid}", read_generation),
            web.post(prefix + "/generation-jobs/{jid}/cancel", cancel_generation),
            web.post(prefix + "/generation-jobs/{jid}/retry", retry_generation),
            web.get(prefix + "/{cid}", read),
            web.post(prefix + "/{cid}", execute),
            web.get(prefix + "/{cid}/export", read),
            web.get(prefix + "/{cid}/preview", read),
            web.post(prefix + "/{cid}/instantiate", instantiate),
        ]
    )

    async def generation_lifespan(application: web.Application) -> AsyncIterator[None]:
        yield
        if ORCHESTRATOR_KEY in application:
            await application[ORCHESTRATOR_KEY].processes.close()

    app.cleanup_ctx.append(generation_lifespan)
