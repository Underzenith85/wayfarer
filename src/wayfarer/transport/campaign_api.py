"""Bearer-authenticated HTTP facade for the authoritative play service."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path

from aiohttp import web
from pydantic import Field

from wayfarer.config import Settings
from wayfarer.engine.character.power import CharacterProposal
from wayfarer.engine.rules.catalog import reference
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.studio import GenerationBrief, ScenarioGraph
from wayfarer.errors import (
    AuthorizationError,
    ConflictError,
    ValidationError,
    WayfarerError,
)
from wayfarer.models import Record
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.advancement import AdvanceCharacter, AdvancementService, GrantPoints
from wayfarer.orchestration.codex import ProviderStatus
from wayfarer.orchestration.director import DirectorService
from wayfarer.orchestration.provider_runtime import provider_runtime
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.orchestration.setup import SetupService
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.orchestration.workshop import DraftCommand, WorkshopService
from wayfarer.orchestration.workshop_options import (
    CharacterPreviewRequest,
    ProfilePreviewRequest,
    ReviewActor,
    ReviewSubmission,
    WorkshopOptions,
    WorkshopReviewQueue,
    catalog_options,
    preview_character,
    preview_profile,
    profile_option,
)
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.transport.common import (
    ACCESS_KEY,
    MAX_BODY,
    ORCHESTRATOR_KEY,
    TOKENS_KEY,
    _identity,
    _json,
)
from wayfarer.transport.setup_api import LEGACY_KEY
from wayfarer.transport.setup_api import generate as generate_setup
from wayfarer.transport.setup_api import install as install_setup
from wayfarer.transport.tactical_api import install as install_tactical
from wayfarer.transport.v1.http import install
from wayfarer.transport.v1.keys import TOKENS
from wayfarer.transport.v1.live import CONNECTIONS
from wayfarer.transport.v1.provider import bind_provider

# The single-page application's own entry routes. Each view is addressable
# bare and per campaign, so a bookmarked or shared link resolves to the app,
# which then authenticates its own requests.
FRONTEND_PAGES = ("/character", "/inventory", "/journal", "/campaign")
FRONTEND_ROUTES = ("/", *FRONTEND_PAGES)
CAMPAIGN_ROUTE = re.compile(rf"/c/[^/]+({'|'.join(FRONTEND_PAGES)})?")


def _entry_document(path: str) -> bool:
    """True for a route that serves the application shell rather than data."""
    return path in FRONTEND_ROUTES or CAMPAIGN_ROUTE.fullmatch(path) is not None


PROVIDER_STATUS_KEY = web.AppKey("provider-status", deque[ProviderStatus])

LIMITS_KEY = web.AppKey("campaign-limits", dict[str, tuple[float, int]])


@web.middleware
async def boundary(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    if request.path.startswith("/api/v1"):
        return await handler(request)
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    try:
        if (
            request.path != "/health"
            and not _entry_document(request.path)
            and not request.path.startswith("/assets/")
        ):
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
    except ValueError, TypeError, KeyError:
        response = web.json_response(
            {"error": "Invalid request", "code": "validation_error"}, status=400
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    return response


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
    proposal: dict[str, object] | None = None
    actor_id: str = Field(min_length=1, max_length=100)
    command_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)


async def interpret(request: web.Request) -> web.Response:
    body = InterpretRequest.model_validate(await _json(request))
    base = request.app[ORCHESTRATOR_KEY]
    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    orchestrator = (
        base
        if access is request.app[ACCESS_KEY]
        else Orchestrator(
            access,
            base.provider,
            timeout=base.timeout,
            attempts=base.attempts,
        )
    )
    result = await DirectorService(orchestrator).run(
        request.match_info["cid"],
        principal_id=_identity(request),
        actor_id=body.actor_id,
        command_id=body.command_id,
        text=body.text,
        proposal=body.proposal,
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


class GenerateDraftRequest(Record):
    command: DraftCommand
    prompt: str = Field(min_length=1, max_length=4000)


async def generate_scenario_draft(request: web.Request) -> web.Response:

    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    state = access.play._load(await access.play.store.read(request.match_info["cid"]))
    principal = _identity(request)
    if access._member(state, principal).role != "gm":
        raise AuthorizationError("Scenario generation requires GM")
    raw = await _json(request)
    command = DraftCommand.model_validate_json(json.dumps(raw.get("command")))
    if command.kind != "scenario" or command.operation != "save":
        raise ValidationError("Scenario save command required")
    brief = GenerationBrief.model_validate_json(json.dumps(raw.get("brief")))
    controlled = {a for m in state.members if m.role == "player" for a in m.actor_ids}
    graph, report = await ScenarioStudio(access.play).generate(
        brief,
        llm=request.app[ORCHESTRATOR_KEY],
        principal_id=principal,
        party=tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal)
            for a in state.actors
            if a.actor_id in controlled
        ),
    )
    saved = await WorkshopService(access).execute(
        request.match_info["cid"],
        command.model_copy(update={"content_json": graph.model_dump_json()}),
        principal_id=principal,
    )
    return web.json_response(
        {"draft": saved, "validation": report.model_dump(mode="json"), "valid": report.valid}
    )


async def generate_draft(request: web.Request) -> web.Response:
    body = GenerateDraftRequest.model_validate_json(json.dumps(await _json(request)))
    result = await WorkshopService(
        await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    ).generate(
        request.match_info["cid"],
        body.command,
        principal_id=_identity(request),
        prompt=body.prompt,
        llm=request.app[ORCHESTRATOR_KEY],
    )
    return web.json_response(result)


async def workshop_start(request: web.Request) -> web.Response:
    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    state = access.play._load(await access.play.store.read(request.match_info["cid"]))
    actor_id = request.match_info["aid"]
    member = access._member(state, _identity(request))
    reviewing = member.role == "gm" and member.principal_id in access.play.engine.reviewer.gm_ids
    if not reviewing:
        access._control(member, actor_id)
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    if actor is None:
        raise AuthorizationError("Character unavailable")
    draft = next(
        (
            d
            for d in reversed(state.drafts)
            if d.actor_id == actor_id
            and (
                d.owner_id == member.principal_id or reviewing and d.submitted_revision is not None
            )
            and d.kind == "character"
            and (not request.query.get("draft_id") or d.id == request.query["draft_id"])
        ),
        None,
    )
    if reviewing and draft is None:
        raise AuthorizationError("Submitted draft unavailable")
    compiler = access.play.engine.reviewer.compiler
    build = compiler.compile(actor.proposal.draft).build
    active = DEFAULT_REGISTRY.find(reference(compiler.rules))
    options = WorkshopOptions(
        active_profile=active.id if active else compiler.statistics_profile,
        active_profile_version=active.version if active else None,
        profiles=tuple(profile_option(p) for p in DEFAULT_REGISTRY.profiles),
        catalog=catalog_options(compiler),
        build_revision=build.revision if build else None,
        points_available=sum(e.points for e in state.advancement if e.actor_id == actor_id),
        can_approve=member.role == "gm"
        and member.principal_id in access.play.engine.reviewer.gm_ids,
        can_edit=actor_id in member.actor_ids,
    )
    return web.json_response(
        {
            "revision": state.revision,
            "options": options.model_dump(mode="json"),
            "proposal": actor.proposal.model_dump(mode="json"),
            "draft": WorkshopService(access)._preview(draft) if draft else None,
            "catalog": [{"id": d.id, "name": d.name} for d in compiler.definitions.values()],
        }
    )


async def workshop_reviews(request: web.Request) -> web.Response:
    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    state = access.play._load(await access.play.store.read(request.match_info["cid"]))
    member = access._member(state, _identity(request))
    if member.role != "gm" or member.principal_id not in access.play.engine.reviewer.gm_ids:
        raise AuthorizationError("Workshop review requires campaign GM")

    controlled = {a for m in state.members if m.role == "player" for a in m.actor_ids}
    result = WorkshopReviewQueue(
        revision=state.revision,
        submissions=tuple(
            ReviewSubmission(
                draft_id=d.id,
                actor_id=d.actor_id,
                owner_id=d.owner_id,
                name=CharacterProposal.model_validate_json(d.content_json).draft.name,
                draft_revision=d.revision,
                approved=d.approval_json is not None,
            )
            for d in state.drafts
            if d.kind == "character"
            and d.submitted_revision is not None
            and d.activated_revision is None
        ),
        actors=tuple(
            ReviewActor(actor_id=a.actor_id, name=a.proposal.draft.name)
            for a in state.actors
            if a.actor_id in controlled
        ),
    )
    return web.json_response(result.model_dump(mode="json"))


async def workshop_grant(request: web.Request) -> web.Response:

    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    cid, principal = request.match_info["cid"], _identity(request)
    state = access.play._load(await access.play.store.read(cid))
    if access._member(state, principal).role != "gm":
        raise AuthorizationError("Point grants require campaign GM")
    body = GrantPoints.model_validate_json(json.dumps(await _json(request)))
    result = await AdvancementService(access.play).grant(cid, body, authenticated_gm_id=principal)
    return web.json_response(result.model_dump(mode="json"))


async def workshop_character_preview(request: web.Request) -> web.Response:
    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    state = access.play._load(await access.play.store.read(request.match_info["cid"]))
    access._control(access._member(state, _identity(request)), request.match_info["aid"])
    body = CharacterPreviewRequest.model_validate_json(json.dumps(await _json(request)))
    return web.json_response(
        preview_character(access.play.engine.reviewer, body.proposal).model_dump(mode="json")
    )


async def workshop_profile_preview(request: web.Request) -> web.Response:
    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    state = access.play._load(await access.play.store.read(request.match_info["cid"]))
    access._member(state, _identity(request))
    body = ProfilePreviewRequest.model_validate_json(json.dumps(await _json(request)))
    return web.json_response(preview_profile(body).model_dump(mode="json"))


async def workshop_advance(request: web.Request) -> web.Response:

    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    cid = request.match_info["cid"]
    state = access.play._load(await access.play.store.read(cid))
    body = AdvanceCharacter.model_validate_json(json.dumps(await _json(request)))
    access._control(access._member(state, _identity(request)), body.actor_id)
    service = AdvancementService(access.play)
    if request.match_info["operation"] == "preview":
        result = await service.preview(cid, body, authenticated_actor_id=body.actor_id)
    elif request.match_info["operation"] == "apply":
        entry = await service.advance(cid, body, authenticated_actor_id=body.actor_id)
        return web.json_response(entry.model_dump(mode="json"))
    else:
        raise ValidationError("Unknown advancement operation")
    return web.json_response(result.model_dump(mode="json"))


async def read_draft(request: web.Request) -> web.Response:
    result = await WorkshopService(
        await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    ).read(request.match_info["cid"], request.match_info["did"], principal_id=_identity(request))
    return web.json_response(result)


async def save_draft(request: web.Request) -> web.Response:
    body = DraftCommand.model_validate_json(json.dumps(await _json(request)))
    result = await WorkshopService(
        await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    ).execute(request.match_info["cid"], body, principal_id=_identity(request))
    return web.json_response(result)


class ActivateScenarioRequest(Record):
    campaign_id: str = Field(min_length=1, max_length=100)
    expected_draft_revision: int = Field(ge=1)


async def activate_scenario(request: web.Request) -> web.Response:

    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    cid = request.match_info["cid"]
    state = access.play._load(await access.play.store.read(cid))
    principal = _identity(request)
    member = access._member(state, principal)
    if member.role != "gm":
        raise AuthorizationError("Scenario activation requires GM")
    body = ActivateScenarioRequest.model_validate(await _json(request))
    draft = WorkshopService(access)._get(state, request.match_info["did"], principal)
    if draft.kind != "scenario" or draft.revision != body.expected_draft_revision:
        raise ConflictError("Scenario draft changed")
    graph = ScenarioGraph.model_validate_json(draft.content_json)
    seed = (await access.play.store.read(cid)).copy()
    seed.pop("play_json", None)
    seed.pop("resources_json", None)
    seed.pop("scenario_graph_json", None)
    seed["id"], seed["revision"] = body.campaign_id, 0
    activated = await ScenarioStudio(access.play).activate(
        graph, seed, state.members, principal_id=principal
    )
    return web.json_response(
        {
            "campaign_id": body.campaign_id,
            "revision": activated._load(await activated.store.read(body.campaign_id)).revision,
        }
    )


async def validate_scenario(request: web.Request) -> web.Response:

    access = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
    state = access.play._load(await access.play.store.read(request.match_info["cid"]))
    if access._member(state, _identity(request)).role != "gm":
        raise AuthorizationError("Scenario authoring requires GM")
    graph = ScenarioGraph.model_validate_json(json.dumps(await _json(request)))
    report = ScenarioStudio(access.play).validate(graph)
    return web.json_response({**report.model_dump(mode="json"), "valid": report.valid})


def create_campaign_app(
    play: CampaignAccess,
    tokens: Mapping[str, str],
    *,
    settings: Settings | None = None,
    v1_ledger_path: Path | None = None,
    v1_origins: frozenset[str] = frozenset(),
    v1_allow_no_origin: bool = False,
    legacy_routes: bool = False,
    frontend_dir: Path | None = None,
    scenario_templates: tuple[ScenarioGraph, ...] = (),
) -> web.Application:
    if not tokens or any(not token or not principal for token, principal in tokens.items()):
        raise ValueError("Non-empty credentials required")
    app = web.Application(middlewares=[boundary], client_max_size=MAX_BODY)
    app[ACCESS_KEY] = play
    app[TOKENS_KEY] = dict(tokens)
    app[LIMITS_KEY] = {}
    install_tactical(app)
    install_setup(app, SetupService(play, engine_controls=legacy_routes), scenario_templates)
    app[LEGACY_KEY] = legacy_routes
    if frontend_dir is not None:

        async def frontend(_: web.Request) -> web.FileResponse:
            return web.FileResponse(frontend_dir / "index.html")

        scoped = ("/c/{campaign}", *(f"/c/{{campaign}}{page}" for page in FRONTEND_PAGES))
        for path in (*FRONTEND_ROUTES, *scoped):
            app.router.add_get(path, frontend)
        app.router.add_static("/assets", frontend_dir / "assets")
    app.router.add_get("/health", health)
    if legacy_routes:
        app.add_routes(
            [
                web.get("/campaigns/{cid}", read_campaign),
                web.post("/campaigns/{cid}/commands", command),
                web.get("/campaigns/{cid}/events", events),
                web.get("/campaigns/{cid}/drafts/{did}", read_draft),
                web.get("/campaigns/{cid}/workshop/{aid}", workshop_start),
                web.post("/campaigns/{cid}/workshop/{aid}/preview", workshop_character_preview),
                web.get("/campaigns/{cid}/workshop-reviews", workshop_reviews),
                web.post("/campaigns/{cid}/workshop-grants", workshop_grant),
                web.post("/campaigns/{cid}/workshop-profile-preview", workshop_profile_preview),
                web.post("/campaigns/{cid}/workshop-advancement/{operation}", workshop_advance),
                web.post("/campaigns/{cid}/drafts", save_draft),
                web.post("/campaigns/{cid}/scenario-validation", validate_scenario),
                web.post("/campaigns/{cid}/drafts/{did}/activate-scenario", activate_scenario),
            ]
        )

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
        app.router.add_post("/setups/{cid}/generate", generate_setup)
        app[PROVIDER_STATUS_KEY] = deque(maxlen=256)

        async def lifespan(application: web.Application) -> AsyncIterator[None]:
            async with provider_runtime(
                settings, status=application[PROVIDER_STATUS_KEY].append
            ) as provider:
                application[ORCHESTRATOR_KEY] = Orchestrator(
                    play, provider, timeout=min(settings.model_timeout_seconds, 300.0), attempts=1
                )

                bind_provider(v1, application[ORCHESTRATOR_KEY])
                yield

        app.cleanup_ctx.append(lifespan)
        if legacy_routes:
            app.add_routes(
                [
                    web.post("/campaigns/{cid}/interpret", interpret),
                    web.post("/campaigns/{cid}/generate-draft", generate_draft),
                    web.post("/campaigns/{cid}/generate-scenario", generate_scenario_draft),
                    web.get("/campaigns/{cid}/provider-status", provider_status),
                ]
            )
    app.cleanup_ctx.append(v1_lifespan)
    return app
