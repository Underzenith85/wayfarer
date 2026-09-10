"""Additive tactical-v1 API; frozen gameplay-v1 remains untouched."""

import json

from aiohttp import web
from pydantic import Field

from wayfarer.errors import ValidationError, WayfarerError
from wayfarer.models import Record
from wayfarer.orchestration.combat import (
    COMBAT_ADAPTER,
    ChooseDefense,
    CombatService,
    ContinueCriticalMiss,
    MigrateEncounterHex,
    RepairEquipment,
    ResolveChokeEffects,
    ResumeInterruptedTurn,
    RetrieveEquipment,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.equipment_view import TacticalSnapshotV2, equipment_view
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import TacticalSnapshot, project, snapshot, visible_actors
from wayfarer.simulation.actions import PlayState
from wayfarer.transport.campaign_api import ACCESS_KEY, _identity, _json
from wayfarer.transport.tactical_v1_commands import ChooseDefense as ChooseDefenseV1
from wayfarer.transport.tactical_v1_commands import TakeCombatTurn as TakeCombatTurnV1
from wayfarer.transport.tactical_v1_commands import TakeUnarmedTurn as TakeUnarmedTurnV1


class TacticalRequest(Record):
    command: (
        TakeCombatTurnV1
        | TakeUnarmedTurnV1
        | ChooseDefenseV1
        | MigrateEncounterHex
        | ResumeInterruptedTurn
        | ResolveChokeEffects
    ) = Field(discriminator="kind")


class TacticalRequestV2(Record):
    command: (
        TakeCombatTurn
        | TakeUnarmedTurn
        | ChooseDefense
        | MigrateEncounterHex
        | ResumeInterruptedTurn
        | ResolveChokeEffects
        | RepairEquipment
        | RetrieveEquipment
        | ContinueCriticalMiss
    ) = Field(discriminator="kind")


async def read(request: web.Request) -> web.Response:
    result = await snapshot(
        request.app[ACCESS_KEY],
        request.match_info["cid"],
        _identity(request),
        request.query.get("actor_id", ""),
    )
    if request.path.startswith("/api/tactical/v2/"):
        runtime = await request.app[ACCESS_KEY].runtime(request.match_info["cid"])
        state = runtime.play._load(await runtime.play.store.read(request.match_info["cid"]))
        runtime._control(runtime._member(state, _identity(request)), result.actor_id)
        result = enrich(
            runtime.play,
            state,
            project(
                runtime.play,
                state,
                runtime._member(state, _identity(request)),
                result.actor_id,
                include_object_choices=True,
            ),
        )
    return web.json_response(result.model_dump(mode="json"))


def enrich(play: PlayService, state: PlayState, result: TacticalSnapshot) -> TacticalSnapshotV2:
    return TacticalSnapshotV2.model_validate_json(
        json.dumps(
            {
                **result.model_dump(mode="json"),
                "version": "tactical-v2",
                "equipment": [
                    v.model_dump(mode="json") for v in equipment_view(play, state, result.actor_id)
                ],
            }
        )
    )


async def execute(request: web.Request) -> web.Response:
    cid, principal = request.match_info["cid"], _identity(request)
    access = await request.app[ACCESS_KEY].runtime(cid)
    state = access.play._load(await access.play.store.read(cid))
    member = access._member(state, principal)
    request_type = (
        TacticalRequestV2 if request.path.startswith("/api/tactical/v2/") else TacticalRequest
    )
    body = request_type.model_validate_json(json.dumps(await _json(request)))
    command = COMBAT_ADAPTER.validate_json(body.command.model_dump_json())
    access._control(member, command.actor_id)
    payload = json.dumps(
        {"operation": "combat", "command": command.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    duplicate = await access.play.store.duplicate(cid, command.id, payload)
    if duplicate is not None:
        result = project(
            access.play,
            state,
            member,
            command.actor_id,
            include_object_choices=request_type is TacticalRequestV2,
        )
        if request_type is TacticalRequestV2:
            result = enrich(access.play, state, result)
        return web.json_response(result.model_dump(mode="json"))
    if state.lifecycle != "active":
        raise ValidationError("Resume the campaign before acting")
    encounter = CombatService._encounter(state, command.encounter_id)
    if isinstance(command, (MigrateEncounterHex, ContinueCriticalMiss)):
        if member.role != "gm":
            raise ValidationError("Migration requires GM authority")
    else:
        if encounter.hex_battlefield is None or command.actor_id not in encounter.turn_order:
            raise ValidationError("Tactical encounter is unavailable")
        visible = visible_actors(state, encounter, command.actor_id)
        if isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)):
            if command.target_id is not None and command.target_id not in visible:
                raise ValidationError("Target is unavailable")
            if isinstance(command, TakeCombatTurn) and command.wait_trigger is not None:
                trigger = command.wait_trigger
                if any(
                    a is not None and a not in visible
                    for a in (trigger.actor_id, trigger.target_id, trigger.reaction_target_id)
                ):
                    raise ValidationError("Target is unavailable")
    try:
        await CombatService(access.play).execute(
            cid, command, authenticated_actor_id=command.actor_id
        )
    except WayfarerError as exc:
        # Do not reflect internal actor IDs, authored secrets or hidden geometry in errors.
        return web.json_response(
            {
                "code": exc.code,
                "error": "Tactical action is unavailable; refresh and choose again.",
            },
            status=exc.status,
        )
    state = access.play._load(await access.play.store.read(cid))
    result = project(
        access.play,
        state,
        member,
        command.actor_id,
        include_object_choices=request_type is TacticalRequestV2,
    )
    if request_type is TacticalRequestV2:
        result = enrich(access.play, state, result)
    return web.json_response(result.model_dump(mode="json"))


def install(app: web.Application) -> None:
    app.add_routes(
        [
            web.get("/api/tactical/v1/campaigns/{cid}", read),
            web.post("/api/tactical/v1/campaigns/{cid}/commands", execute),
            web.get("/api/tactical/v2/campaigns/{cid}", read),
            web.post("/api/tactical/v2/campaigns/{cid}/commands", execute),
        ]
    )
