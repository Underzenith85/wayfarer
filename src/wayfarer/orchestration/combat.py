"""Transactional adapter for the explicit combat encounter state machine."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.combat import (
    CombatResult,
    Defense,
    Encounter,
    Facing,
    GridPoint,
    Maneuver,
    Placement,
    Posture,
)
from wayfarer.simulation.resources import Id, Record


class CombatCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class StartEncounter(CombatCommand):
    kind: Literal["start_encounter"] = "start_encounter"
    encounter_id: Id
    battlefield_id: Id
    placements: tuple[Placement, ...] = Field(min_length=2)


class TakeCombatTurn(CombatCommand):
    kind: Literal["take_combat_turn"] = "take_combat_turn"
    encounter_id: Id
    maneuver: Maneuver
    destination: GridPoint | None = None
    facing: Facing | None = None
    posture: Posture | None = None
    item_id: str | None = None
    target_id: str | None = None


class ChooseDefense(CombatCommand):
    kind: Literal["choose_defense"] = "choose_defense"
    encounter_id: Id
    defense: Defense


class EndEncounter(CombatCommand):
    kind: Literal["end_encounter"] = "end_encounter"
    encounter_id: Id
    reason: str = Field(min_length=1, max_length=1000)


TypedCombatCommand = Annotated[
    StartEncounter | TakeCombatTurn | ChooseDefense | EndEncounter,
    Field(discriminator="kind"),
]
COMBAT_ADAPTER: TypeAdapter[TypedCombatCommand] = TypeAdapter(TypedCombatCommand)


class CombatService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    @staticmethod
    def propose(value: object) -> TypedCombatCommand:
        try:
            return COMBAT_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid combat command") from exc

    @staticmethod
    def _encounter(state: PlayState, encounter_id: str) -> Encounter:
        encounter = next((e for e in state.encounters if e.id == encounter_id), None)
        if encounter is None:
            raise ValidationError("Unknown encounter")
        return encounter

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> CombatResult:
        command = self.propose(value)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Combat command actor is not authorized")
        engine = self.play.engine.combat
        if engine is None:
            raise ValidationError("Campaign combat is not configured")
        if isinstance(command, (StartEncounter, EndEncounter)) and (
            command.actor_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Encounter lifecycle requires GM authority")
        payload = json.dumps(
            {"operation": "combat", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        duplicate = await self.play.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            result = self.play._load(duplicate).last_combat_result
            if result is None or result.encounter_id != command.encounter_id:
                raise ValidationError("Missing committed combat result")
            return result

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            if command.expected_revision != state.revision:
                raise ConflictError("Play revision changed")
            resources = state.resources
            if isinstance(command, StartEncounter):
                if any(e.id == command.encounter_id for e in state.encounters):
                    raise ConflictError("Encounter ID already exists")
                actor_map = {actor.actor_id: actor for actor in state.actors}
                if len({p.actor_id for p in command.placements}) != len(command.placements) or any(
                    p.actor_id not in actor_map for p in command.placements
                ):
                    raise ValidationError("Encounter placements require unique play actors")
                initiatives: dict[str, int] = {}
                pools = {pool.id: pool for pool in state.resources.pools}
                for placement in command.placements:
                    actor = actor_map[placement.actor_id]
                    hp = pools.get(f"hp:{actor.actor_id}")
                    if actor.conditions or hp is None or hp.current == 0:
                        raise ValidationError("Incapacitated actor cannot start combat")
                    build, _ = self.play.engine.reviewer.activate(
                        actor.proposal,
                        actor.approval,
                        campaign_id=state.campaign_id,
                        actor_id=actor.actor_id,
                    )
                    values = {value.target: int(value.value) for value in build.sheet.values}
                    initiatives[actor.actor_id] = values["attribute:dx"]
                encounter = engine.start(
                    command.encounter_id,
                    command.battlefield_id,
                    command.placements,
                    initiatives,
                    state.world,
                    resources,
                    frozenset(actor_map),
                )
                encounters = state.encounters + (encounter,)
                result = CombatResult(
                    encounter_id=encounter.id,
                    code="combat.started",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    available=engine.available(encounter, encounter.current_actor_id),
                )
            else:
                encounter = self._encounter(state, command.encounter_id)
                if isinstance(command, TakeCombatTurn):
                    encounter, resources, result = engine.take_turn(
                        encounter,
                        actor_id=command.actor_id,
                        maneuver=command.maneuver,
                        resources=resources,
                        destination=command.destination,
                        facing=command.facing,
                        posture=command.posture,
                        item_id=command.item_id,
                        target_id=command.target_id,
                        command_id=command.id,
                    )
                elif isinstance(command, ChooseDefense):
                    encounter, result = engine.choose_defense(
                        encounter, actor_id=command.actor_id, selected=command.defense
                    )
                else:
                    if encounter.status != "active" or encounter.pending_defense is not None:
                        raise ConflictError("Encounter cannot end during a pending defense")
                    encounter = encounter.model_copy(
                        update={"status": "completed", "completion_reason": command.reason}
                    )
                    result = CombatResult(
                        encounter_id=encounter.id,
                        code="combat.completed",
                        round=encounter.round,
                        current_actor_id=encounter.current_actor_id,
                    )
                encounters = tuple(
                    encounter if e.id == encounter.id else e for e in state.encounters
                )
            revision = state.revision + 1
            resources = resources.model_copy(update={"revision": revision})
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "resources": resources,
                    "encounters": encounters,
                    "last_combat_result": result,
                    "rulings": expire_rulings(state.rulings, revision, resources.game_time),
                }
            )
            self.play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = revision, updated.model_dump_json()
            return Event(
                input=payload, action="combat", outcome=result.model_dump_json(), roll=None
            )

        committed = await self.play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
        )
        result = self.play._load(committed["state"]).last_combat_result
        if result is None:
            raise ValidationError("Missing committed combat result")
        return result
