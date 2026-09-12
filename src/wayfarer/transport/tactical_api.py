"""Additive tactical-v1 API; frozen gameplay-v1 remains untouched."""

import hashlib
import json

from aiohttp import web
from pydantic import Field

from wayfarer.engine.simulation.access import CampaignMember
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat import BasicSpatialContext, Encounter, basic_visible
from wayfarer.engine.simulation.encounter_context import activity_for
from wayfarer.errors import ValidationError, WayfarerError
from wayfarer.models import Record
from wayfarer.orchestration.combat import (
    COMBAT_ADAPTER,
    ChooseDefense,
    CombatService,
    ContinueCriticalMiss,
    DeclareBasicSpatialFacts,
    DeclareThrownLanding,
    JoinEncounter,
    MigrateEncounterBasic,
    MigrateEncounterHex,
    RepairEquipment,
    ResolveChokeEffects,
    ResolveWeaponExplosion,
    ResumeInterruptedTurn,
    RetrieveEquipment,
    StartBasicEncounter,
    TakeCombatTurn,
    TakeUnarmedTurn,
    WithdrawEncounter,
    preview_withdrawal,
)
from wayfarer.orchestration.equipment_view import (
    BasicTacticalActor,
    BasicTacticalEncounter,
    TacticalActivity,
    TacticalSnapshotV2,
    equipment_view,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import (
    TacticalSnapshot,
    project,
    snapshot,
    visible_actors,
)
from wayfarer.orchestration.tactical_view import (
    choices as tactical_choices,
)
from wayfarer.transport.campaign_api import ACCESS_KEY, _identity, _json
from wayfarer.transport.tactical_v1_commands import ChooseDefense as ChooseDefenseV1
from wayfarer.transport.tactical_v1_commands import MigrateEncounterHex as MigrateEncounterHexV1
from wayfarer.transport.tactical_v1_commands import TakeCombatTurn as TakeCombatTurnV1
from wayfarer.transport.tactical_v1_commands import TakeUnarmedTurn as TakeUnarmedTurnV1


class TacticalRequest(Record):
    command: (
        TakeCombatTurnV1
        | TakeUnarmedTurnV1
        | ChooseDefenseV1
        | MigrateEncounterHexV1
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
        | DeclareThrownLanding
        | ResolveWeaponExplosion
        | JoinEncounter
        | MigrateEncounterBasic
        | WithdrawEncounter
        | StartBasicEncounter
        | DeclareBasicSpatialFacts
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
        member = runtime._member(state, _identity(request))
        runtime._control(member, result.actor_id)
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
            member,
        )
    return web.json_response(result.model_dump(mode="json"))


def enrich(
    play: PlayService,
    state: PlayState,
    result: TacticalSnapshot,
    member: CampaignMember | None = None,
) -> TacticalSnapshotV2:
    encounter = next(
        (
            encounter
            for encounter in state.encounters
            if encounter.spatial_kind == "hex" and result.actor_id in encounter.turn_order
        ),
        None,
    )
    migrations = (
        [
            {
                "label": "Convert to Basic combat",
                "command": {
                    "kind": "migrate_encounter_basic",
                    "id": f"basic:{state.revision}:{encounter.id}",
                    "actor_id": member.principal_id,
                    "expected_revision": state.revision,
                    "encounter_id": encounter.id,
                },
            }
        ]
        if member is not None
        and member.role == "gm"
        and encounter is not None
        and state.lifecycle == "active"
        else []
    )
    active = next(
        (
            encounter
            for encounter in state.encounters
            if encounter.status == "active" and result.actor_id in encounter.turn_order
        ),
        None,
    )
    withdrawals: list[dict[str, object]] = []
    if active is not None and state.lifecycle == "active":
        digest = hashlib.sha256(
            f"{state.campaign_id}:{state.revision}:{active.id}:{result.actor_id}".encode()
        ).hexdigest()
        command = WithdrawEncounter(
            id=f"withdraw:{digest}",
            actor_id=result.actor_id,
            expected_revision=state.revision,
            encounter_id=active.id,
            new_group_id=f"withdrawn:{digest}",
        )
        try:
            preview_withdrawal(play, state, active, command)
        except WayfarerError, ValueError:
            pass
        else:
            withdrawals.append(
                {"label": "Leave combat", "command": command.model_dump(mode="json")}
            )
    entities = {entity.id: entity.name for entity in state.world.entities}
    basic_encounters: list[BasicTacticalEncounter] = []
    for basic in state.encounters:
        if not isinstance(basic.spatial, BasicSpatialContext):
            continue
        if member is None or member.role != "gm":
            if result.actor_id not in basic.turn_order:
                continue
            visible = {result.actor_id}
            for participant in basic.participants:
                try:
                    if basic_visible(basic, result.actor_id, participant.actor_id):
                        visible.add(participant.actor_id)
                except ValidationError:
                    continue
        else:
            visible = set(basic.turn_order)
        basic_encounters.append(
            BasicTacticalEncounter(
                id=basic.id,
                status=basic.status,
                round=basic.round,
                current_actor_id=(
                    basic.current_actor_id if basic.current_actor_id in visible else None
                ),
                actors=tuple(
                    BasicTacticalActor(
                        id=participant.actor_id,
                        name=entities[participant.actor_id],
                        controlled=participant.actor_id == result.actor_id,
                        posture=participant.posture,
                        grappled=participant.grappled,
                        pinned=participant.pinned,
                    )
                    for participant in basic.participants
                    if participant.actor_id in visible
                ),
                choices=(
                    tactical_choices(play, state, basic, result.actor_id, frozenset(visible))
                    if result.actor_id in basic.turn_order
                    else ()
                ),
                notice=(
                    "A combat decision is pending."
                    if basic.pending_defense or basic.pending_unarmed or basic.wait_interrupt
                    else "Spatial clarification is required for actions not listed."
                ),
            )
        )
    actor_activity = (
        activity_for(state, result.actor_id)
        if any(actor.actor_id == result.actor_id for actor in state.actors)
        else None
    )
    actor_encounter = actor_activity.encounter if actor_activity else None
    group = actor_activity.group if actor_activity else None
    queued = actor_activity.queued is not None if actor_activity else False
    activity = TacticalActivity(
        kind="combat"
        if actor_encounter
        else "waiting"
        if queued or group and group.paused
        else "independent",
        representation=actor_encounter.spatial_kind if actor_encounter else None,
        message=(
            f"Active {actor_encounter.spatial_kind} combat."
            if actor_encounter
            else "Your group is paused at a shared-time boundary."
            if group and group.paused
            else "Independent activity is queued."
            if queued
            else "No active combat; independent scene activity is available."
        ),
        ready_through=group.ready_through if group else state.resources.game_time,
        paused=group.paused if group else False,
    )
    return TacticalSnapshotV2.model_validate_json(
        json.dumps(
            {
                **result.model_dump(mode="json"),
                "version": "tactical-v2",
                "equipment": [
                    v.model_dump(mode="json") for v in equipment_view(play, state, result.actor_id)
                ],
                "migrations": migrations,
                "withdrawals": withdrawals,
                "basic_encounters": [
                    encounter.model_dump(mode="json") for encounter in basic_encounters
                ],
                "activity": activity.model_dump(mode="json"),
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
            result = enrich(access.play, state, result, member)
        return web.json_response(result.model_dump(mode="json"))
    if state.lifecycle != "active":
        raise ValidationError("Resume the campaign before acting")
    encounter = (
        None
        if isinstance(command, StartBasicEncounter)
        else CombatService._encounter(state, command.encounter_id)
    )
    if isinstance(command, StartBasicEncounter):
        if member.role != "gm":
            raise ValidationError("Combat setup requires GM authority")
    elif isinstance(
        command,
        (
            MigrateEncounterHex,
            MigrateEncounterBasic,
            DeclareBasicSpatialFacts,
            ContinueCriticalMiss,
            DeclareThrownLanding,
            ResolveWeaponExplosion,
        ),
    ):
        if member.role != "gm":
            raise ValidationError("GM combat workflow requires GM authority")
    elif isinstance(command, WithdrawEncounter):
        assert encounter is not None
        if command.actor_id not in encounter.turn_order:
            raise ValidationError("Combat withdrawal is unavailable")
    elif isinstance(command, JoinEncounter):
        assert encounter is not None
        if command.joining_actor_id is not None and member.role != "gm":
            raise ValidationError("GM admission requires GM authority")
    else:
        assert encounter is not None
        if (
            encounter.spatial_kind not in ("basic", "hex")
            or command.actor_id not in encounter.turn_order
        ):
            raise ValidationError("Tactical encounter is unavailable")
        if encounter.spatial_kind == "basic":
            visible = frozenset(
                participant.actor_id
                for participant in encounter.participants
                if participant.actor_id == command.actor_id
                or _basic_visible(encounter, command.actor_id, participant.actor_id)
            )
        else:
            visible = visible_actors(
                state,
                encounter,
                command.actor_id,
                board=access.play.rules_context.hex_map(encounter),
            )
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
    access = await access.runtime(cid)
    state = access.play._load(await access.play.store.read(cid))
    result = project(
        access.play,
        state,
        member,
        command.actor_id,
        include_object_choices=request_type is TacticalRequestV2,
    )
    if request_type is TacticalRequestV2:
        result = enrich(access.play, state, result, member)
    return web.json_response(result.model_dump(mode="json"))


def _basic_visible(encounter: Encounter, subject_id: str, object_id: str) -> bool:
    try:
        return basic_visible(encounter, subject_id, object_id)
    except ValidationError:
        return False


def install(app: web.Application) -> None:
    app.add_routes(
        [
            web.get("/api/tactical/v1/campaigns/{cid}", read),
            web.post("/api/tactical/v1/campaigns/{cid}/commands", execute),
            web.get("/api/tactical/v2/campaigns/{cid}", read),
            web.post("/api/tactical/v2/campaigns/{cid}/commands", execute),
        ]
    )
