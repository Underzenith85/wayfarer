"""Authenticated, fixed-scope Roadside Watch application on either real store."""

from collections.abc import Mapping
from pathlib import Path

from aiohttp import web

from wayfarer.adventures.roadwatch import adventure, engine
from wayfarer.adventures.roadwatch_care import POLICY as CARE_POLICY
from wayfarer.adventures.roadwatch_rules import GM_ID, PLAYER_ID
from wayfarer.adventures.roadwatch_scope import RoadwatchScope, admission
from wayfarer.engine.simulation.combat.critical import CriticalMiss
from wayfarer.errors import AuthorizationError
from wayfarer.orchestration.entropy import SeedSource, token_seed
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.runtime import CampaignRuntime
from wayfarer.orchestration.sessions import Store
from wayfarer.transport.campaign_api import create_campaign_app
from wayfarer.transport.common import RUNTIME_KEY, _identity


async def gm_projection(request: web.Request) -> web.Response:
    """Real GM-only receipt/context discovery, never a private-database runbook."""
    runtime = await request.app[RUNTIME_KEY].for_campaign(request.match_info["cid"])
    principal = _identity(request)
    state = await runtime.checkpoint(request.match_info["cid"])
    member = runtime.member(state, principal)
    if member.role != "gm" or not runtime.directs(principal):
        raise AuthorizationError("Roadside Watch's GM projection requires trusted GM membership")
    blocked = {
        encounter.id: encounter for encounter in state.encounters if encounter.blocked_reason
    }
    criticals: list[dict[str, object]] = []
    for event in state.resources.events:
        if not event.id.startswith("critical:"):
            continue
        record = CriticalMiss.model_validate_json(event.kind)
        encounter = blocked.get(record.encounter_id)
        if encounter is not None and encounter.blocked_reason == record.blocker:
            criticals.append(
                {
                    "critical_id": record.id,
                    "encounter_id": record.encounter_id,
                    "subject_id": record.subject_id,
                    "item_id": record.item_id,
                    "table_total": record.table_total,
                    "blocker": record.blocker,
                    "context": record.model_dump(mode="json"),
                }
            )
    events = await runtime.events(
        state.campaign_id,
        principal_id=principal,
        after=int(request.query.get("after", "0")),
        limit=int(request.query.get("limit", "100")),
    )
    return web.json_response(
        {
            "campaign_id": state.campaign_id,
            "revision": state.revision,
            "role": "gm",
            "pending_critical_misses": criticals,
            "events": [event.model_dump(mode="json") for event in events],
        }
    )


def application(
    store: Store,
    tokens: Mapping[str, str],
    *,
    v1_ledger_path: Path | None = None,
    seeds: SeedSource = token_seed,
    frontend: Path | None = None,
    origins: frozenset[str] = frozenset(),
) -> web.Application:
    """Compose the shipped host; deterministic seeds are explicit trusted DI only."""
    if not {PLAYER_ID, GM_ID} <= set(tokens.values()):
        raise ValueError("Roadside Watch requires separate party and gm bearer identities")
    graph = adventure()
    runtime = CampaignRuntime(
        PlayService(store, engine(), seeds=seeds), medical_environment=CARE_POLICY
    )
    app = create_campaign_app(
        runtime,
        tokens,
        v1_ledger_path=v1_ledger_path,
        v1_origins=origins,
        v1_allow_no_origin=True,
        engine_controls=True,
        frontend_dir=frontend,
        scenario_templates=(graph,),
    )
    # Authentication middleware remains outside scope admission on all transports.
    app.middlewares.append(admission(RoadwatchScope(graph)))
    app.router.add_get("/roadwatch/campaigns/{cid}/gm", gm_projection)
    return app
