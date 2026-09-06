"""Explicit combat lifecycle and action economy for the supported prototype subset.

This module deliberately stops before attack/defense/damage resolution. An attack
intent persists a defense-choice pause; Wave 9 owns the mechanical outcome.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.simulation.resources import Equip, Id, Record, ResourceEngine, ResourceState
from wayfarer.world import EntityKind, World

Facing = Literal["north", "east", "south", "west"]
Posture = Literal["standing", "kneeling", "prone"]
Maneuver = Literal["do_nothing", "move", "ready", "change_posture", "attack", "wait"]
Defense = Literal["dodge", "parry", "block", "none"]


class GridPoint(Record):
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)


class Battlefield(Record):
    id: Id
    location_id: Id
    width: int = Field(ge=1, le=1000)
    height: int = Field(ge=1, le=1000)
    blocked: tuple[GridPoint, ...] = ()

    @model_validator(mode="after")
    def validate_grid(self) -> Battlefield:
        if len(set(self.blocked)) != len(self.blocked):
            raise ValueError("Duplicate blocked position")
        if any(point.x >= self.width or point.y >= self.height for point in self.blocked):
            raise ValueError("Blocked position is outside the battlefield")
        return self


class CombatRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_allowance: int = Field(default=5, ge=1, le=100)
    prone_movement_allowance: int = Field(default=1, ge=0, le=100)
    default_reach: int = Field(default=1, ge=1, le=20)
    max_combatants: int = Field(default=30, ge=2, le=100)
    battlefields: tuple[Battlefield, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique(self) -> CombatRules:
        if len({b.id for b in self.battlefields}) != len(self.battlefields):
            raise ValueError("Duplicate battlefield ID")
        return self


class Placement(Record):
    actor_id: Id
    position: GridPoint
    facing: Facing = "north"


class Combatant(Record):
    actor_id: Id
    initiative: int = Field(ge=0, le=100)
    position: GridPoint
    facing: Facing
    posture: Posture = "standing"
    reach: int = Field(ge=1, le=20)
    movement_allowance: int = Field(ge=1, le=100)
    ready_item_ids: tuple[str, ...] = ()
    reaction_available: bool = True
    last_maneuver: Maneuver | None = None


class PendingDefense(Record):
    id: Id
    attacker_id: Id
    defender_id: Id
    weapon_id: Id
    allowed: tuple[Defense, ...] = Field(min_length=1)
    opened_round: int = Field(ge=1)
    opened_turn: int = Field(ge=0)


class DefenseChoice(Record):
    pending: PendingDefense
    selected: Defense
    chosen_by: Id


class Encounter(Record):
    id: Id
    battlefield_id: Id
    status: Literal["active", "completed"] = "active"
    participants: tuple[Combatant, ...] = Field(min_length=2)
    turn_order: tuple[str, ...] = Field(min_length=2)
    round: int = Field(default=1, ge=1)
    turn_index: int = Field(default=0, ge=0)
    pending_defense: PendingDefense | None = None
    defense_history: tuple[DefenseChoice, ...] = ()
    completion_reason: str | None = None

    @property
    def current_actor_id(self) -> str:
        return self.turn_order[self.turn_index]


class CombatResult(Record):
    encounter_id: Id
    code: str
    round: int = Field(ge=1)
    current_actor_id: Id
    pending_defense_id: str | None = None
    available: tuple[str, ...] = ()


class CombatEngine:
    def __init__(self, rules: CombatRules, resources: ResourceEngine) -> None:
        self.rules = CombatRules.model_validate(rules)
        self.resources = resources
        self.battlefields = {b.id: b for b in self.rules.battlefields}

    @staticmethod
    def distance(left: GridPoint, right: GridPoint) -> int:
        return abs(left.x - right.x) + abs(left.y - right.y)

    def validate(
        self,
        encounter: Encounter,
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> None:
        battlefield = self.battlefields.get(encounter.battlefield_id)
        if battlefield is None:
            raise ValidationError("Encounter battlefield is not configured")
        entities = {entity.id: entity for entity in world.entities}
        participants = {p.actor_id: p for p in encounter.participants}
        if (
            len(participants) != len(encounter.participants)
            or not 2 <= len(participants) <= self.rules.max_combatants
            or set(participants) != set(encounter.turn_order)
            or len(set(encounter.turn_order)) != len(encounter.turn_order)
            or encounter.turn_index >= len(encounter.turn_order)
            or not set(participants) <= actor_ids
        ):
            raise ValidationError("Invalid encounter participants or turn order")
        expected_order = tuple(
            p.actor_id
            for p in sorted(encounter.participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        if encounter.turn_order != expected_order:
            raise ValidationError("Turn order does not match initiative")
        occupied: set[GridPoint] = set()
        blocked = set(battlefield.blocked)
        ready = {
            (item.owner_id, item.id) for item in resources.items if item.equipped and item.ready
        }
        for participant in encounter.participants:
            entity = entities.get(participant.actor_id)
            if (
                entity is None
                or entity.kind is not EntityKind.ACTOR
                or (encounter.status == "active" and entity.location_id != battlefield.location_id)
            ):
                raise ValidationError("Combatant is not at the battlefield location")
            if (
                participant.position.x >= battlefield.width
                or participant.position.y >= battlefield.height
                or participant.position in blocked
                or participant.position in occupied
            ):
                raise ValidationError("Combatant position is blocked, occupied or out of bounds")
            occupied.add(participant.position)
            if participant.movement_allowance != self.rules.movement_allowance:
                raise ValidationError("Combat movement is not rules-authored")
            if participant.reach != self.rules.default_reach:
                raise ValidationError("Combat reach is not rules-authored")
            expected_ready = tuple(
                sorted(item_id for owner_id, item_id in ready if owner_id == participant.actor_id)
            )
            if encounter.status == "active" and participant.ready_item_ids != expected_ready:
                raise ValidationError("Combat readiness disagrees with inventory")
        pending = encounter.pending_defense
        if pending is not None:
            if (
                encounter.status != "active"
                or pending.attacker_id != encounter.current_actor_id
                or pending.attacker_id not in participants
                or pending.defender_id not in participants
                or pending.attacker_id == pending.defender_id
                or pending.opened_round != encounter.round
                or pending.opened_turn != encounter.turn_index
                or pending.weapon_id not in participants[pending.attacker_id].ready_item_ids
                or len(set(pending.allowed)) != len(pending.allowed)
                or "none" not in pending.allowed
            ):
                raise ValidationError("Invalid pending defense pause")
        historical_ids: set[str] = set()
        for choice in encounter.defense_history:
            historical = choice.pending
            if (
                historical.id in historical_ids
                or (pending is not None and historical.id == pending.id)
                or historical.attacker_id not in participants
                or historical.defender_id not in participants
                or historical.attacker_id == historical.defender_id
                or choice.chosen_by != historical.defender_id
                or choice.selected not in historical.allowed
                or historical.opened_round > encounter.round
                or historical.opened_turn >= len(encounter.turn_order)
            ):
                raise ValidationError("Invalid defense history")
            historical_ids.add(historical.id)
        if encounter.status == "completed" and (
            pending is not None or not encounter.completion_reason
        ):
            raise ValidationError("Completed encounter has unfinished state")
        if encounter.status == "active" and encounter.completion_reason is not None:
            raise ValidationError("Active encounter cannot have a completion reason")

    def start(
        self,
        encounter_id: str,
        battlefield_id: str,
        placements: tuple[Placement, ...],
        initiatives: dict[str, int],
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> Encounter:
        participants = tuple(
            Combatant(
                actor_id=p.actor_id,
                initiative=initiatives[p.actor_id],
                position=p.position,
                facing=p.facing,
                reach=self.rules.default_reach,
                movement_allowance=self.rules.movement_allowance,
                ready_item_ids=tuple(
                    sorted(
                        item.id
                        for item in resources.items
                        if item.owner_id == p.actor_id and item.equipped and item.ready
                    )
                ),
            )
            for p in placements
        )
        order = tuple(
            p.actor_id for p in sorted(participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        encounter = Encounter(
            id=encounter_id,
            battlefield_id=battlefield_id,
            participants=participants,
            turn_order=order,
        )
        self.validate(encounter, world, resources, actor_ids)
        return encounter

    def available(self, encounter: Encounter, actor_id: str) -> tuple[str, ...]:
        if encounter.status != "active":
            return ()
        pending = encounter.pending_defense
        if pending is not None:
            return tuple(pending.allowed) if actor_id == pending.defender_id else ()
        if actor_id != encounter.current_actor_id:
            return ()
        return ("do_nothing", "move", "ready", "change_posture", "attack", "wait")

    @staticmethod
    def _replace(encounter: Encounter, participant: Combatant) -> Encounter:
        return encounter.model_copy(
            update={
                "participants": tuple(
                    participant if p.actor_id == participant.actor_id else p
                    for p in encounter.participants
                )
            }
        )

    @staticmethod
    def _reachable(
        battlefield: Battlefield,
        start: GridPoint,
        destination: GridPoint,
        limit: int,
        occupied: set[GridPoint],
    ) -> bool:
        blocked = set(battlefield.blocked) | occupied
        frontier = {start}
        visited = {start}
        for _ in range(limit):
            next_frontier: set[GridPoint] = set()
            for point in frontier:
                for x, y in (
                    (point.x - 1, point.y),
                    (point.x + 1, point.y),
                    (point.x, point.y - 1),
                    (point.x, point.y + 1),
                ):
                    if not (0 <= x < battlefield.width and 0 <= y < battlefield.height):
                        continue
                    neighbor = GridPoint(x=x, y=y)
                    if neighbor in blocked or neighbor in visited:
                        continue
                    if neighbor == destination:
                        return True
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
            frontier = next_frontier
        return start == destination

    @staticmethod
    def _advance(encounter: Encounter) -> Encounter:
        index = encounter.turn_index + 1
        round_number = encounter.round
        if index == len(encounter.turn_order):
            index = 0
            round_number += 1
        return encounter.model_copy(
            update={
                "turn_index": index,
                "round": round_number,
                "participants": tuple(
                    p.model_copy(update={"reaction_available": True})
                    if p.actor_id == encounter.turn_order[index]
                    else p
                    for p in encounter.participants
                ),
            }
        )

    def take_turn(
        self,
        encounter: Encounter,
        *,
        actor_id: str,
        maneuver: Maneuver,
        resources: ResourceState,
        destination: GridPoint | None = None,
        facing: Facing | None = None,
        posture: Posture | None = None,
        item_id: str | None = None,
        target_id: str | None = None,
        command_id: str,
    ) -> tuple[Encounter, ResourceState, CombatResult]:
        if encounter.status != "active" or encounter.pending_defense is not None:
            raise ConflictError("Encounter cannot accept a maneuver now")
        if actor_id != encounter.current_actor_id:
            raise ConflictError("Combat action is out of turn")
        participant = next(p for p in encounter.participants if p.actor_id == actor_id)
        battlefield = self.battlefields[encounter.battlefield_id]
        if maneuver == "move":
            if destination is None or any(
                value is not None for value in (posture, item_id, target_id)
            ):
                raise ValidationError("Move requires only a destination and optional facing")
            limit = (
                self.rules.prone_movement_allowance
                if participant.posture == "prone"
                else participant.movement_allowance
            )
            occupied = {p.position for p in encounter.participants if p.actor_id != actor_id}
            if (
                destination.x >= battlefield.width
                or destination.y >= battlefield.height
                or destination in battlefield.blocked
                or destination in occupied
                or self.distance(participant.position, destination) > limit
                or not self._reachable(
                    battlefield, participant.position, destination, limit, occupied
                )
            ):
                raise ValidationError("Combat movement exceeds allowance or terrain constraints")
            participant = participant.model_copy(
                update={
                    "position": destination,
                    "facing": facing or participant.facing,
                    "last_maneuver": maneuver,
                }
            )
        elif maneuver == "ready":
            if item_id is None or any(
                value is not None for value in (destination, facing, posture, target_id)
            ):
                raise ValidationError("Ready requires exactly one item")
            resources = self.resources.apply(
                resources,
                Equip(
                    id=f"{command_id}:ready",
                    actor_id=actor_id,
                    expected_revision=resources.revision,
                    item_id=item_id,
                    ready=True,
                ),
            )
            participant = participant.model_copy(
                update={
                    "ready_item_ids": tuple(
                        sorted(
                            item.id
                            for item in resources.items
                            if item.owner_id == actor_id and item.equipped and item.ready
                        )
                    ),
                    "last_maneuver": maneuver,
                }
            )
        elif maneuver == "change_posture":
            if posture is None or any(
                value is not None for value in (destination, facing, item_id, target_id)
            ):
                raise ValidationError("Posture maneuver requires exactly one posture")
            participant = participant.model_copy(
                update={"posture": posture, "last_maneuver": maneuver}
            )
        elif maneuver == "attack":
            if (
                target_id is None
                or item_id is None
                or any(value is not None for value in (destination, facing, posture))
            ):
                raise ValidationError("Attack intent requires a target and ready weapon")
            target = next((p for p in encounter.participants if p.actor_id == target_id), None)
            if (
                target is None
                or target.actor_id == actor_id
                or item_id not in participant.ready_item_ids
                or self.distance(participant.position, target.position) > participant.reach
            ):
                raise ValidationError("Attack target or weapon is unavailable or out of reach")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
            encounter = self._replace(encounter, participant)
            pending = PendingDefense(
                id=f"{command_id}:defense",
                attacker_id=actor_id,
                defender_id=target_id,
                weapon_id=item_id,
                allowed=("dodge", "parry", "none") if target.ready_item_ids else ("dodge", "none"),
                opened_round=encounter.round,
                opened_turn=encounter.turn_index,
            )
            encounter = encounter.model_copy(update={"pending_defense": pending})
            return (
                encounter,
                resources,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.defense_required",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    pending_defense_id=pending.id,
                    available=tuple(pending.allowed),
                ),
            )
        else:
            if any(
                value is not None for value in (destination, facing, posture, item_id, target_id)
            ):
                raise ValidationError("Maneuver has unexpected parameters")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        encounter = self._advance(self._replace(encounter, participant))
        return (
            encounter,
            resources,
            CombatResult(
                encounter_id=encounter.id,
                code=f"combat.{maneuver}",
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                available=self.available(encounter, encounter.current_actor_id),
            ),
        )

    def choose_defense(
        self, encounter: Encounter, *, actor_id: str, selected: Defense
    ) -> tuple[Encounter, CombatResult]:
        pending = encounter.pending_defense
        if pending is None:
            raise ConflictError("Encounter is not waiting for a defense")
        if actor_id != pending.defender_id or selected not in pending.allowed:
            raise ValidationError("Defense is not available to this actor")
        defender = next(p for p in encounter.participants if p.actor_id == actor_id)
        if not defender.reaction_available and selected != "none":
            raise ValidationError("Defender has no reaction available")
        defender = defender.model_copy(
            update={"reaction_available": False if selected != "none" else True}
        )
        choice = DefenseChoice(pending=pending, selected=selected, chosen_by=actor_id)
        encounter = self._replace(encounter, defender).model_copy(
            update={
                "pending_defense": None,
                "defense_history": encounter.defense_history + (choice,),
            }
        )
        encounter = self._advance(encounter)
        return (
            encounter,
            CombatResult(
                encounter_id=encounter.id,
                code="combat.defense_recorded",
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                available=self.available(encounter, encounter.current_actor_id),
            ),
        )
