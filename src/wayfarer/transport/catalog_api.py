"""Scenario authoring API v1: additive to, and independent of, frozen play v1."""

import asyncio
from collections.abc import AsyncIterator

from aiohttp import web
from pydantic import Field

from wayfarer.errors import ConflictError, ProviderError, ProviderTimeoutError, ValidationError
from wayfarer.orchestration.catalog import ScenarioCatalog
from wayfarer.orchestration.scenario_documents import adapt_graph
from wayfarer.simulation.catalog import (
    CatalogCommand,
    InstantiateRevision,
    ScenarioGenerationJob,
    ScenarioGenerationRequest,
)
from wayfarer.simulation.resources import Record
from wayfarer.simulation.scenario_document import PublicBrief
from wayfarer.transport.campaign_api import _identity
from wayfarer.transport.setup_api import TEMPLATES_KEY

KEY = web.AppKey("scenario-catalog", ScenarioCatalog)
TASKS_KEY = web.AppKey("scenario-generation-tasks", dict[str, asyncio.Task[None]])
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


async def _generate(app: web.Application, principal: str, job_id: str) -> None:
    from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY

    service = app[KEY]
    try:
        await service.run_generation_job(principal, job_id, app[ORCHESTRATOR_KEY])
    except asyncio.CancelledError:
        return
    except (ProviderTimeoutError, ProviderError, ValidationError, ValueError) as exc:
        try:
            job = await service.read_generation_job(principal, job_id)
            if job.status != "cancelled":
                code = (
                    "provider_timeout"
                    if isinstance(exc, ProviderTimeoutError)
                    else "provider_unavailable"
                    if isinstance(exc, ProviderError)
                    else "invalid_provider_output"
                )
                message = (
                    "The provider timed out. Retry when it is available."
                    if code == "provider_timeout"
                    else "The provider is unavailable or limited. Check its login and retry."
                    if code == "provider_unavailable"
                    else "The provider returned an invalid scenario. Edit the brief and retry."
                )
                await service.store.update_job(
                    job.model_copy(
                        update={"status": "failed", "error_code": code, "error_message": message}
                    ),
                    job.version,
                )
        except ConflictError:
            pass
    finally:
        app[TASKS_KEY].pop(job_id, None)


def _start(app: web.Application, principal: str, job: ScenarioGenerationJob) -> None:
    if job.status == "queued" and job.id not in app[TASKS_KEY]:
        app[TASKS_KEY][job.id] = asyncio.create_task(
            _generate(app, principal, job.id), name=f"scenario-generation:{job.id}"
        )


async def create_generation(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY

    if ORCHESTRATOR_KEY not in request.app:
        raise ProviderError("Scenario generation is unavailable; choose a template or manual draft")
    principal = _identity(request)
    job = await request.app[KEY].create_generation_job(
        principal, ScenarioGenerationRequest.model_validate_json(await body(request))
    )
    _start(request.app, principal, job)
    return web.json_response(job.model_dump(mode="json"), status=202)


async def read_generation(request: web.Request) -> web.Response:
    principal = _identity(request)
    job = await request.app[KEY].read_generation_job(principal, request.match_info["jid"])
    # A queued/running row with no local task survived a restart. Make recovery explicit.
    if job.status in ("queued", "running") and job.id not in request.app[TASKS_KEY]:
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
    return web.json_response(job.model_dump(mode="json"))


async def cancel_generation(request: web.Request) -> web.Response:
    principal = _identity(request)
    job = await request.app[KEY].cancel_generation_job(principal, request.match_info["jid"])
    task = request.app[TASKS_KEY].get(job.id)
    if task:
        task.cancel()
    return web.json_response(job.model_dump(mode="json"))


class RetryRequest(Record):
    expected_version: int = Field(ge=1)


async def retry_generation(request: web.Request) -> web.Response:
    from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY

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
    _start(request.app, principal, job)
    return web.json_response(job.model_dump(mode="json"), status=202)


def install(app: web.Application, service: ScenarioCatalog) -> None:
    app[KEY] = service
    app[TASKS_KEY] = {}
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
        tasks = tuple(application[TASKS_KEY].values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app.cleanup_ctx.append(generation_lifespan)