"""Explicit combat lifecycle and action economy for the supported prototype subset.

Attack intent persists a defense-choice pause. Configured original prototype
profiles resolve checks, protection and injury atomically when that choice resumes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.effects import DerivedValue
from wayfarer.rules.location_types import HitLocation, HumanLocation
from wayfarer.simulation.gurps_equipment import EquipmentCatalog
from wayfarer.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.simulation.maneuvers import (
    ATTACK_MANEUVERS,
    AttackOption,
    DefenseOption,
    ManeuverState,
    WaitInterrupt,
    WaitTrigger,
)
from wayfarer.simulation.resources import Equip, Id, Record, ResourceEngine, ResourceState
from wayfarer.simulation.tactical import TacticalTrace
from wayfarer.simulation.unarmed import Grip, PendingUnarmed, UnarmedTrace
from wayfarer.world import EntityKind, World

Facing = Literal["north", "east", "south", "west"]
Posture = Literal["standing", "kneeling", "prone"]
Maneuver = Literal[
    "do_nothing",
    "move",
    "ready",
    "change_posture",
    "attack",
    "wait",
    "concentrate",
    "aim",
    "evaluate",
    "feint",
    "all_out_attack",
    "all_out_defense",
    "move_and_attack",
]
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


class AttackProfile(Record):
    """Server-authored original subset, bound to an implemented equipment entry."""

    definition_id: Id
    mode: Literal["melee"] = "melee"
    target: Literal["torso"] = "torso"
    attack_target: Literal["attribute:dx"] = "attribute:dx"
    attack_modifier: int = Field(default=0, ge=-20, le=20)
    damage_dice: int = Field(default=1, ge=1, le=10)
    damage_bonus: int = Field(default=0, ge=-10, le=100)
    injury_multiplier: int = Field(default=1, ge=1, le=4)
    stun_ticks: int = Field(default=1, ge=0, le=100)


class ProtectionProfile(Record):
    definition_id: Id
    resistance: int = Field(ge=0, le=100)


class InjuryTrace(Record):
    attack: CheckTrace
    defense: CheckTrace | None = None
    second_defense: CheckTrace | None = None
    attack_value: DerivedValue
    defense_value: DerivedValue | None = None
    damage_dice: tuple[int, ...] = ()
    basic_damage: int = Field(default=0, ge=0)
    resistance: int = Field(default=0, ge=0)
    injury: int = Field(default=0, ge=0)
    hp_before: int
    hp_after: int
    incapacitated: bool = False
    stunned_until: int | None = None
    profile_id: Id
    rules_version: str
    critical_table: tuple[int, ...] = ()
    adjudication_required: str | None = None
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    lasting_injury_ids: tuple[str, ...] = ()
    shots_fired: int = Field(default=0, ge=0)
    hits: int = Field(default=0, ge=0)
    per_hit_damage: tuple[int, ...] = ()
    per_hit_injury: tuple[int, ...] = ()
    per_hit_resistance: tuple[int, ...] = Field(default=(), exclude_if=lambda v: not v)
    per_hit_locations: tuple[HumanLocation | None, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    per_hit_location_dice: tuple[tuple[int, ...], ...] = Field(
        default=(), exclude_if=lambda v: not v
    )


class CombatConsequence(Record):
    """Authored revelation after recorded incapacitation in a completed encounter."""

    id: Id
    battlefield_id: Id
    defeated_actor_id: Id
    recipient_actor_ids: tuple[Id, ...] = Field(min_length=1)
    fact_ids: tuple[Id, ...] = Field(min_length=1)


class CombatRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_allowance: int = Field(default=5, ge=1, le=100)
    prone_movement_allowance: int = Field(default=1, ge=0, le=100)
    default_reach: int = Field(default=1, ge=1, le=20)
    max_combatants: int = Field(default=30, ge=2, le=100)
    battlefields: tuple[Battlefield, ...] = Field(min_length=1, max_length=100)

    consequences: tuple[CombatConsequence, ...] = Field(default=(), exclude=True)
    attacks: tuple[AttackProfile, ...] = Field(default=(), exclude=True)
    protection: tuple[ProtectionProfile, ...] = Field(default=(), exclude=True)
    gurps_equipment: EquipmentCatalog | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_unique(self) -> CombatRules:
        if len({b.id for b in self.battlefields}) != len(self.battlefields):
            raise ValueError("Duplicate battlefield ID")
        if len({p.definition_id for p in self.attacks}) != len(self.attacks) or len(
            {p.definition_id for p in self.protection}
        ) != len(self.protection):
            raise ValueError("Duplicate combat profile")
        return self


class Placement(Record):
    actor_id: Id
    position: GridPoint
    facing: Facing = "north"


class Combatant(Record):
    actor_id: Id
    initiative: int = Field(ge=0, le=100)
    position: GridPoint | Hex
    facing: Facing
    hex_facing: Literal[0, 1, 2, 3, 4, 5] | None = None
    retreat_used: bool = False
    retreat_attacker_id: str | None = None
    tactical_defense_bonus: int = 0
    posture: Posture = "standing"
    reach: int = Field(ge=1, le=20)
    movement_allowance: int = Field(ge=0, le=100)
    ready_item_ids: tuple[str, ...] = ()
    reaction_available: bool = True
    parries: tuple[str, ...] = ()
    block_used: bool = False
    defense_penalty: int = Field(default=0, ge=-20, le=0)
    last_maneuver: Maneuver | None = None
    last_attack_item_id: str | None = None
    hand_bindings: tuple[tuple[str, Literal["left-hand", "right-hand"]], ...] = ()
    arm_locked: bool = False
    grappled: bool = False
    pinned: bool = False
    forced_do_nothing: bool = False
    maneuver_state: ManeuverState = Field(default_factory=ManeuverState)


class PendingDefense(Record):
    id: Id
    attacker_id: Id
    defender_id: Id
    weapon_id: Id
    allowed: tuple[Defense, ...] = Field(min_length=1)
    shots: int = Field(default=1, ge=1)
    opened_round: int = Field(ge=1)
    opened_turn: int = Field(ge=0)
    mode_id: str | None = None
    hit_location: HitLocation | None = None
    target_item_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    spell_cast_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    post_attack_destination: GridPoint | None = None
    post_attack_square_facing: Facing | None = None
    post_attack_hex_path: tuple[Hex, ...] = ()
    post_attack_facing: Literal[0, 1, 2, 3, 4, 5] | None = None
    post_attack_posture: Posture | None = None


class DefenseChoice(Record):
    pending: PendingDefense
    selected: Defense
    chosen_by: Id


class RangedSituation(Record):
    attacker_id: Id
    defender_id: Id
    distance_yards: float = Field(gt=0, allow_inf_nan=False)
    speed_yards_per_second: float = Field(default=0, ge=0, allow_inf_nan=False)
    size_modifier: int = 0


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
    wounds: tuple[InjuryTrace, ...] = ()
    blocked_reason: str | None = None
    wait_interrupt: WaitInterrupt | None = None
    grips: tuple[Grip, ...] = ()
    close_pairs: tuple[tuple[str, str], ...] = ()
    pending_unarmed: PendingUnarmed | None = None
    unarmed_history: tuple[UnarmedTrace, ...] = ()
    ranged_situations: tuple[RangedSituation, ...] = ()
    hex_battlefield: HexBattlefield | None = None
    tactical_traces: tuple[TacticalTrace, ...] = ()

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
    injury: InjuryTrace | None = None
    unarmed: UnarmedTrace | None = None


class CombatEngine:
    def __init__(self, rules: CombatRules, resources: ResourceEngine) -> None:
        self.rules = CombatRules.model_validate(rules)
        self.resources = resources
        if any(p.definition_id not in resources.specs for p in rules.attacks) or any(
            p.definition_id not in resources.specs for p in rules.protection
        ):
            raise ValidationError("Combat profile requires implemented catalog equipment")
        self.battlefields = {b.id: b for b in self.rules.battlefields}

    @staticmethod
    def distance(left: GridPoint | Hex, right: GridPoint | Hex) -> int:
        if isinstance(left, Hex) and isinstance(right, Hex):
            from wayfarer.simulation.hex_geometry import distance

            return distance(left, right)
        if not isinstance(left, GridPoint) or not isinstance(right, GridPoint):
            raise ValidationError("Coordinate systems require explicit migration")
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
        if encounter.hex_battlefield is not None:
            from wayfarer.simulation.tactical import validate_hex_encounter

            validate_hex_encounter(encounter, self.rules.gurps_equipment)
        occupied: set[GridPoint | Hex] = set()
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
                (
                    isinstance(participant.position, GridPoint)
                    and (
                        participant.position.x >= battlefield.width
                        or participant.position.y >= battlefield.height
                    )
                )
                or (encounter.hex_battlefield is None and isinstance(participant.position, Hex))
                or participant.position in blocked
                or (
                    participant.position in occupied
                    and not (
                        self.rules.gurps_equipment is not None
                        and self.rules.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
                        and all(
                            tuple(sorted((other.actor_id, participant.actor_id)))
                            in encounter.close_pairs
                            for other in encounter.participants
                            if other.actor_id != participant.actor_id
                            and other.position == participant.position
                        )
                    )
                )
            ):
                raise ValidationError("Combatant position is blocked, occupied or out of bounds")
            occupied.add(participant.position)
            if (
                self.rules.gurps_equipment is None
                and participant.movement_allowance != self.rules.movement_allowance
            ):
                raise ValidationError("Combat movement is not rules-authored")
            if self.rules.gurps_equipment is None and participant.reach != self.rules.default_reach:
                raise ValidationError("Combat reach is not rules-authored")
            expected_ready = tuple(
                sorted(item_id for owner_id, item_id in ready if owner_id == participant.actor_id)
            )
            if encounter.status == "active" and participant.ready_item_ids != expected_ready:
                raise ValidationError("Combat readiness disagrees with inventory")
        from wayfarer.simulation.unarmed import validate_control

        validate_control(
            encounter,
            resources,
            basic=(
                self.rules.gurps_equipment is not None
                and self.rules.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
            ),
        )
        pending = encounter.pending_defense
        if pending is not None:
            spell_weapon = False
            if pending.spell_cast_id is not None:
                from wayfarer.simulation.spells import active_spells

                spell_weapon = any(
                    e.execute_effects
                    and e.spell_id == "fireball"
                    and e.cast_id == pending.spell_cast_id
                    and e.actor_id == pending.attacker_id
                    and pending.weapon_id
                    == "spell:" + hashlib.sha256(e.cast_id.encode()).hexdigest()
                    for e in active_spells(resources)
                )
                if not spell_weapon:
                    raise ValidationError("Pending missile has no held spell")
            if (
                encounter.status != "active"
                or pending.attacker_id != encounter.current_actor_id
                or pending.attacker_id not in participants
                or pending.defender_id not in participants
                or pending.attacker_id == pending.defender_id
                or pending.opened_round != encounter.round
                or pending.opened_turn != encounter.turn_index
                or (
                    not spell_weapon
                    and pending.weapon_id not in participants[pending.attacker_id].ready_item_ids
                )
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
        if encounter.status != "active" or encounter.blocked_reason:
            return ()
        if encounter.pending_unarmed is not None:
            pause = encounter.pending_unarmed
            return tuple(pause.allowed) if actor_id == pause.target_id else ()
        pending = encounter.pending_defense
        if pending is not None:
            return tuple(pending.allowed) if actor_id == pending.defender_id else ()
        interrupt = encounter.wait_interrupt
        if interrupt is not None:
            if interrupt.ready:
                return ("resume_interrupted_turn",) if actor_id == interrupt.actor_id else ()
            waiter = next(p for p in encounter.participants if p.actor_id == interrupt.waiter_id)
            reaction = (
                "attack"
                if interrupt.declaration.reaction == "all_out_attack"
                and waiter.maneuver_state.defended
                else interrupt.declaration.reaction
            )
            return (reaction, "do_nothing") if actor_id == interrupt.waiter_id else ()
        if actor_id != encounter.current_actor_id:
            return ()
        base = ("do_nothing", "move", "ready", "change_posture", "attack", "wait")
        if self.rules.gurps_equipment is not None:
            return base + (
                "aim",
                "evaluate",
                "feint",
                "all_out_attack",
                "all_out_defense",
                "move_and_attack",
                "concentrate",
            )
        return base

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
        interrupt = encounter.wait_interrupt
        if interrupt is not None and interrupt.reacting:
            return encounter.model_copy(
                update={
                    "turn_index": interrupt.turn_index,
                    "wait_interrupt": interrupt.model_copy(
                        update={"reacting": False, "ready": True}
                    ),
                }
            )
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
                    p.model_copy(
                        update={
                            "reaction_available": True,
                            "parries": (),
                            "block_used": False,
                            "defense_penalty": 0,
                            "retreat_used": False,
                            "retreat_attacker_id": None,
                            "tactical_defense_bonus": 0,
                            "maneuver_state": p.maneuver_state.new_turn(),
                        }
                    )
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
        attack_option: AttackOption | None = None,
        defense_option: DefenseOption | None = None,
        wait_trigger: WaitTrigger | None = None,
        step_timing: Literal["before", "after"] = "before",
        second_item_id: str | None = None,
        second_target_id: str | None = None,
        second_mode_id: str | None = None,
        command_json: str = "",
        hex_path: tuple[Hex, ...] = (),
        hex_facing: Literal[0, 1, 2, 3, 4, 5] | None = None,
    ) -> tuple[Encounter, ResourceState, CombatResult]:
        original, original_resources = encounter, resources
        interrupt = encounter.wait_interrupt
        if interrupt is not None:
            if interrupt.ready or interrupt.reacting or actor_id != interrupt.waiter_id:
                raise ConflictError("Resolve the interrupted Wait before another action")
            declaration = interrupt.declaration
            reacting_waiter = next(p for p in encounter.participants if p.actor_id == actor_id)
            if declaration.reaction == "all_out_attack" and reacting_waiter.maneuver_state.defended:
                declaration = declaration.model_copy(
                    update={"reaction": "attack", "attack_option": None}
                )
            if declaration.unarmed is not None and maneuver != "do_nothing":
                raise ValidationError("The declared Wait reaction is an unarmed attack")
            if maneuver != "do_nothing" and (maneuver, item_id, target_id, attack_option) != (
                declaration.reaction,
                declaration.item_id,
                declaration.reaction_target_id,
                declaration.attack_option,
            ):
                raise ValidationError("Wait reaction must match its recorded declaration")
            if maneuver != "do_nothing" and (
                step_timing != "before"
                or any(v is not None for v in (second_item_id, second_target_id, second_mode_id))
            ):
                raise ValidationError("Wait reaction includes undeclared maneuver options")
            encounter = encounter.model_copy(
                update={
                    "turn_index": encounter.turn_order.index(actor_id),
                    "wait_interrupt": interrupt.model_copy(update={"reacting": True}),
                }
            )
        result = self._take_turn(
            encounter,
            actor_id=actor_id,
            maneuver=maneuver,
            resources=resources,
            destination=destination,
            facing=facing,
            posture=posture,
            item_id=item_id,
            target_id=target_id,
            command_id=command_id,
            attack_option=attack_option,
            defense_option=defense_option,
            wait_trigger=wait_trigger,
            step_timing=step_timing,
            second_item_id=second_item_id,
            second_target_id=second_target_id,
            second_mode_id=second_mode_id,
            hex_path=hex_path,
            hex_facing=hex_facing,
        )
        if self.rules.gurps_equipment is not None and interrupt is None and command_json:
            action = "attack" if maneuver in ATTACK_MANEUVERS else maneuver
            waiters = {p.actor_id: p for p in original.participants if p.actor_id != actor_id}
            for waiter_id in original.turn_order:
                waiter = waiters.get(waiter_id)
                trigger = waiter.maneuver_state.wait if waiter else None
                if trigger:
                    assert waiter is not None
                    before_actor = next(p for p in original.participants if p.actor_id == actor_id)
                    after_actor = next(p for p in result[0].participants if p.actor_id == actor_id)
                    zone_hit = not trigger.zone or any(
                        (point.q, point.r) in trigger.zone
                        for point in (
                            hex_path
                            or (
                                (after_actor.position,)
                                if isinstance(after_actor.position, Hex)
                                else ()
                            )
                        )
                    )
                    stop_candidate = trigger.stop_thrust and (
                        action == "attack"
                        and target_id == waiter_id
                        and self.distance(before_actor.position, after_actor.position) >= 1
                        and self.distance(after_actor.position, waiter.position)
                        < self.distance(before_actor.position, waiter.position)
                    )
                    if stop_candidate and waiter.reach <= before_actor.reach:
                        raise ValidationError(
                            "Equal or shorter-reach stop-thrust ordering is unsupported"
                        )
                    stop_thrust = stop_candidate and waiter.reach > before_actor.reach
                    observable = True
                    if original.hex_battlefield is not None:
                        from wayfarer.simulation.tactical import sight

                        observable = sight(result[0], waiter, after_actor)
                    matches = (
                        (trigger.actor_id is None or trigger.actor_id == actor_id)
                        and trigger.action == action
                        and (trigger.target_id is None or trigger.target_id == target_id)
                        and zone_hit
                        and observable
                        and (not trigger.stop_thrust or stop_thrust)
                    )
                else:
                    matches = False
                if matches:
                    assert waiter is not None and trigger is not None
                    declaration = trigger
                    bonus = (
                        self.distance(before_actor.position, after_actor.position) // 2
                        if declaration.stop_thrust
                        else 0
                    )
                    moved = self.distance(before_actor.position, after_actor.position) >= 1
                    paused = self._replace(
                        original,
                        waiter.model_copy(
                            update={
                                "maneuver_state": waiter.maneuver_state.model_copy(
                                    update={
                                        "wait": None,
                                        "stop_thrust_damage_bonus": bonus,
                                    }
                                )
                            }
                        ),
                    )
                    resume_json = command_json
                    if moved:
                        paused_actor = before_actor.model_copy(
                            update={
                                "position": after_actor.position,
                                "facing": after_actor.facing,
                                "hex_facing": after_actor.hex_facing,
                                "posture": after_actor.posture,
                            }
                        )
                        paused = self._replace(paused, paused_actor)
                        saved = json.loads(command_json)
                        saved.update(
                            {
                                "destination": None,
                                "facing": None,
                                "posture": None,
                                "hex_path": [],
                                "hex_facing": None,
                                "step_timing": "before",
                            }
                        )
                        if maneuver == "move":
                            saved["maneuver"] = "do_nothing"
                        resume_json = json.dumps(saved, sort_keys=True, separators=(",", ":"))
                    paused = paused.model_copy(
                        update={
                            "wait_interrupt": WaitInterrupt(
                                waiter_id=waiter_id,
                                actor_id=actor_id,
                                turn_index=original.turn_index,
                                command_json=resume_json,
                                declaration=declaration,
                            )
                        }
                    )
                    return (
                        paused,
                        original_resources,
                        CombatResult(
                            encounter_id=paused.id,
                            code="combat.wait_triggered",
                            round=paused.round,
                            current_actor_id=actor_id,
                            available=self.available(paused, waiter_id),
                        ),
                    )
        return result

    def _take_turn(
        self,
        encounter: Encounter,
        *,
        actor_id: str,
        maneuver: Maneuver,
        resources: ResourceState,
        command_id: str,
        destination: GridPoint | None = None,
        facing: Facing | None = None,
        posture: Posture | None = None,
        item_id: str | None = None,
        target_id: str | None = None,
        attack_option: AttackOption | None = None,
        defense_option: DefenseOption | None = None,
        wait_trigger: WaitTrigger | None = None,
        step_timing: Literal["before", "after"] = "before",
        second_item_id: str | None = None,
        second_target_id: str | None = None,
        second_mode_id: str | None = None,
        hex_path: tuple[Hex, ...] = (),
        hex_facing: Literal[0, 1, 2, 3, 4, 5] | None = None,
    ) -> tuple[Encounter, ResourceState, CombatResult]:
        if self.rules.gurps_equipment is not None:
            command_id = "combat:" + hashlib.sha256(command_id.encode()).hexdigest()
        if (
            encounter.status != "active"
            or encounter.pending_defense is not None
            or encounter.blocked_reason
        ):
            raise ConflictError("Encounter cannot accept a maneuver now")
        if actor_id != encounter.current_actor_id:
            raise ConflictError("Combat action is out of turn")
        if maneuver == "concentrate" and self.rules.gurps_equipment is None:
            raise ValidationError("Concentration requires a bound ability command")
        participant = next(p for p in encounter.participants if p.actor_id == actor_id)
        battlefield = self.battlefields[encounter.battlefield_id]
        deferred_step = step_timing == "after"
        if deferred_step and maneuver != "attack":
            raise ValidationError("Only Attack permits a step after the attack")
        if deferred_step and not (
            destination is not None or hex_path or hex_facing is not None or posture is not None
        ):
            raise ValidationError("A post-attack step requires movement, facing, or posture")
        if deferred_step and encounter.hex_battlefield is not None:
            from wayfarer.simulation.tactical import move_hex

            if destination is not None or facing is not None:
                raise ValidationError("Hex encounters require explicit hex paths and facings")
            if posture is not None and (hex_path or hex_facing is not None):
                raise ValidationError("A posture step cannot also move or turn")
            if posture is not None and {posture, participant.posture} != {
                "standing",
                "kneeling",
            }:
                raise ValidationError("A posture step only switches standing and kneeling")
            if posture is None:
                move_hex(encounter, participant, "attack", hex_path, hex_facing, None)
        elif deferred_step and destination is not None:
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square step requires square coordinates")
            occupied = {
                p.position
                for p in encounter.participants
                if p.actor_id != actor_id and isinstance(p.position, GridPoint)
            }
            limit = max(1, (participant.movement_allowance + 9) // 10)
            if not self._reachable(battlefield, participant.position, destination, limit, occupied):
                raise ValidationError("Post-attack step exceeds allowance or terrain constraints")
        elif (
            deferred_step
            and posture is not None
            and {
                posture,
                participant.posture,
            }
            != {"standing", "kneeling"}
        ):
            raise ValidationError("A posture step only switches standing and kneeling")
        if encounter.hex_battlefield is not None and not deferred_step:
            from wayfarer.simulation.tactical import move_hex

            if destination is not None or facing is not None:
                raise ValidationError("Hex encounters require explicit hex paths and facings")
            if posture is not None and hex_path:
                raise ValidationError("A posture step cannot also translate the actor")
            participant = move_hex(
                encounter, participant, maneuver, hex_path, hex_facing, defense_option
            )
            encounter = self._replace(encounter, participant)
        elif (hex_path or hex_facing is not None) and not deferred_step:
            raise ValidationError("Hex movement requires explicit battlefield migration")
        if self.rules.gurps_equipment is None and (
            maneuver not in ("do_nothing", "move", "ready", "change_posture", "attack", "wait")
            or attack_option
            or defense_option
            or wait_trigger
        ):
            raise ValidationError("Maneuver requires exact GURPS profile dispatch")
        if (
            (attack_option is not None and maneuver != "all_out_attack")
            or (defense_option is not None and maneuver != "all_out_defense")
            or (wait_trigger is not None and maneuver != "wait")
        ):
            raise ValidationError("Maneuver options do not match the maneuver")
        if step_timing == "after" and maneuver != "attack":
            raise ValidationError("Post-attack movement requires Attack")
        if self.rules.gurps_equipment is not None:
            old = participant.maneuver_state
            if participant.last_maneuver != "evaluate":
                old = old.model_copy(update={"evaluate_target_id": None, "evaluate_bonus": 0})
            if participant.last_maneuver != "feint":
                old = old.model_copy(update={"feint_target_id": None, "feint_penalty": 0})
            commitment = ManeuverState()
            if maneuver in ATTACK_MANEUVERS or maneuver == "feint":
                commitment = commitment.model_copy(
                    update={
                        "aim_item_id": old.aim_item_id,
                        "aim_target_id": old.aim_target_id,
                        "aim_mode_id": old.aim_mode_id,
                        "aim_seconds": old.aim_seconds if participant.last_maneuver == "aim" else 0,
                        "aim_accuracy": old.aim_accuracy,
                        "aim_braced": old.aim_braced,
                        "aim_sight_bonus": old.aim_sight_bonus,
                        "evaluate_target_id": old.evaluate_target_id,
                        "evaluate_bonus": old.evaluate_bonus,
                        "feint_target_id": old.feint_target_id,
                        "feint_penalty": old.feint_penalty,
                        "stop_thrust_damage_bonus": old.stop_thrust_damage_bonus,
                    }
                )
            if maneuver == "all_out_attack":
                if attack_option is None:
                    raise ValidationError("Choose an All-Out Attack option")
                commitment = commitment.model_copy(
                    update={
                        "defense_forbidden": True,
                        "attack_bonus": 4 if attack_option == "determined" else 0,
                        "strong": attack_option == "strong",
                        "attacks_remaining": int(attack_option == "double"),
                        "second_attack_item_id": second_item_id,
                        "second_attack_target_id": second_target_id,
                        "second_attack_mode_id": second_mode_id,
                    }
                )
                if attack_option == "double":
                    if second_target_id not in (None, target_id):
                        raise ValidationError("All-Out Attack (Double) attacks the same foe")
                    if second_item_id is not None:
                        hands = {item: hand for item, hand in participant.hand_bindings}
                        if (
                            second_item_id == item_id
                            or second_item_id not in participant.ready_item_ids
                            or item_id not in hands
                            or second_item_id not in hands
                            or hands[item_id] == hands[second_item_id]
                        ):
                            raise ValidationError(
                                "Double requires two distinct ready one-hand weapons"
                            )
                        commitment = commitment.model_copy(
                            update={
                                "second_attack_target_id": target_id,
                                "attack_bonus": -4 if hands[item_id] == "left-hand" else 0,
                                "second_attack_penalty": -4
                                if hands[second_item_id] == "left-hand"
                                else 0,
                            }
                        )
                elif any(v is not None for v in (second_item_id, second_target_id, second_mode_id)):
                    raise ValidationError("Second attack choices require All-Out Attack (Double)")
            if maneuver == "move_and_attack":
                commitment = commitment.model_copy(
                    update={"attack_bonus": -4, "attack_cap": 9, "parry_forbidden": True}
                )
            if maneuver in ATTACK_MANEUVERS:
                commitment = commitment.model_copy(
                    update={
                        "aim_item_id": old.aim_item_id,
                        "aim_target_id": old.aim_target_id,
                        "aim_mode_id": old.aim_mode_id,
                        "aim_seconds": old.aim_seconds if participant.last_maneuver == "aim" else 0,
                        "aim_accuracy": old.aim_accuracy,
                        "aim_braced": old.aim_braced,
                        "aim_sight_bonus": old.aim_sight_bonus,
                        "evaluate_target_id": old.evaluate_target_id,
                        "evaluate_bonus": old.evaluate_bonus,
                        "feint_target_id": old.feint_target_id,
                        "feint_penalty": old.feint_penalty,
                        "stop_thrust_damage_bonus": old.stop_thrust_damage_bonus,
                    }
                )
            if maneuver == "concentrate":
                commitment = commitment.model_copy(
                    update={
                        "concentrating": True,
                        "concentration_seconds": old.concentration_seconds + 1
                        if old.concentrating
                        else 1,
                    }
                )
            if maneuver == "all_out_defense":
                if defense_option is None:
                    raise ValidationError("Choose the enhanced active defense")
                commitment = commitment.model_copy(update={"enhanced_defense": defense_option})
            if maneuver == "wait":
                if (
                    wait_trigger is None
                    or wait_trigger.actor_id == actor_id
                    or (
                        wait_trigger.actor_id is not None
                        and wait_trigger.actor_id not in encounter.turn_order
                    )
                ):
                    raise ValidationError("Wait requires an observable other combatant trigger")
                from wayfarer.simulation.spells import active_spells

                held_missile = any(
                    effect.actor_id == actor_id
                    and effect.spell_id == "fireball"
                    and effect.execute_effects
                    and wait_trigger.item_id
                    == "spell:" + hashlib.sha256(effect.cast_id.encode()).hexdigest()
                    for effect in active_spells(resources)
                )
                if held_missile and (
                    wait_trigger.reaction != "attack" or wait_trigger.mode_id is not None
                ):
                    raise ValidationError("Held missile Wait supports its declared release only")
                if (
                    wait_trigger.unarmed is None
                    and wait_trigger.item_id not in participant.ready_item_ids
                    and wait_trigger.reaction != "ready"
                    and not held_missile
                ):
                    raise ValidationError("Wait attack requires a ready weapon")
                if (
                    wait_trigger.reaction != "ready"
                    and wait_trigger.reaction_target_id not in encounter.turn_order
                ):
                    raise ValidationError("Wait attack requires a declared target")
                if (wait_trigger.reaction == "all_out_attack") != (
                    wait_trigger.attack_option is not None
                ):
                    raise ValidationError("Wait All-Out Attack requires its option in advance")
                if wait_trigger.zone:
                    if encounter.hex_battlefield is None:
                        raise ValidationError("Wait zones require an explicit hex battlefield")
                    cells = {
                        (cell.position.q, cell.position.r)
                        for cell in encounter.hex_battlefield.cells
                    }
                    if not set(wait_trigger.zone) <= cells:
                        raise ValidationError("Wait zone is outside the battlefield")
                if wait_trigger.stop_thrust:
                    if (
                        wait_trigger.actor_id is None
                        or wait_trigger.reaction_target_id != wait_trigger.actor_id
                    ):
                        raise ValidationError("Stop thrust requires one declared charging foe")
                commitment = commitment.model_copy(update={"wait": wait_trigger})
            if maneuver in ("evaluate", "aim", "feint"):
                target = next((p for p in encounter.participants if p.actor_id == target_id), None)
                if target is None or target.actor_id == actor_id:
                    raise ValidationError("Maneuver requires another combatant target")
                reach = participant.reach + (
                    participant.movement_allowance if maneuver == "evaluate" else 0
                )
                if (
                    maneuver != "aim"
                    and self.distance(participant.position, target.position) > reach
                ):
                    raise ValidationError("Maneuver target is outside melee reach")
                if maneuver == "evaluate":
                    commitment = commitment.model_copy(
                        update={
                            "evaluate_target_id": target_id,
                            "evaluate_bonus": min(3, old.evaluate_bonus + 1)
                            if old.evaluate_target_id == target_id
                            else 1,
                        }
                    )
                elif maneuver == "aim":
                    if item_id not in participant.ready_item_ids:
                        raise ValidationError("Aim requires a ready ranged weapon")
                    commitment = commitment.model_copy(
                        update={
                            "aim_item_id": item_id,
                            "aim_target_id": target_id,
                            "aim_seconds": min(3, old.aim_seconds + 1)
                            if (old.aim_item_id, old.aim_target_id) == (item_id, target_id)
                            else 1,
                        }
                    )
            participant = participant.model_copy(update={"maneuver_state": commitment})
            step_maneuvers = {
                "attack",
                "aim",
                "evaluate",
                "feint",
                "ready",
                "concentrate",
                "all_out_defense",
            }
            if facing is not None and maneuver in step_maneuvers:
                participant = participant.model_copy(update={"facing": facing})
                facing = None
            if posture is not None and maneuver in step_maneuvers:
                if destination is not None or {posture, participant.posture} != {
                    "standing",
                    "kneeling",
                }:
                    raise ValidationError(
                        "A posture step only switches standing and kneeling in place"
                    )
                participant = participant.model_copy(update={"posture": posture})
                posture = None
            # A single destination is one movement allowance, never a second action.
            if destination is not None and maneuver != "move" and not deferred_step:
                limit = max(1, (participant.movement_allowance + 9) // 10)
                if maneuver == "move_and_attack":
                    limit = participant.movement_allowance
                elif maneuver == "all_out_attack" or (
                    maneuver == "all_out_defense" and defense_option == "dodge"
                ):
                    limit = participant.movement_allowance // 2
                elif maneuver not in (
                    "attack",
                    "aim",
                    "evaluate",
                    "feint",
                    "ready",
                    "concentrate",
                    "all_out_defense",
                ):
                    raise ValidationError("Maneuver does not permit a step")
                if not isinstance(participant.position, GridPoint):
                    raise ValidationError("Square movement requires square coordinates")
                occupied = {
                    p.position
                    for p in encounter.participants
                    if p.actor_id != actor_id and isinstance(p.position, GridPoint)
                }
                if not self._reachable(
                    battlefield, participant.position, destination, limit, occupied
                ):
                    raise ValidationError(
                        "Maneuver movement exceeds allowance or terrain constraints"
                    )
                if maneuver == "all_out_attack":
                    dx, dy = (
                        destination.x - participant.position.x,
                        destination.y - participant.position.y,
                    )
                    forward = {
                        "north": dy < 0 and dx == 0,
                        "south": dy > 0 and dx == 0,
                        "east": dx > 0 and dy == 0,
                        "west": dx < 0 and dy == 0,
                    }
                    if destination != participant.position and not forward[participant.facing]:
                        raise ValidationError("All-Out Attack movement must be forward")
                participant = participant.model_copy(update={"position": destination})
                destination = None
        if maneuver == "move" and encounter.hex_battlefield is not None:
            if any(value is not None for value in (posture, item_id, target_id)):
                raise ValidationError("Move accepts only a path and facing")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        elif maneuver == "move":
            if destination is None or any(
                value is not None for value in (posture, item_id, target_id)
            ):
                raise ValidationError("Move requires only a destination and optional facing")
            limit = (
                self.rules.prone_movement_allowance
                if participant.posture == "prone"
                else participant.movement_allowance
            )
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square movement requires square coordinates")
            occupied = {
                p.position
                for p in encounter.participants
                if p.actor_id != actor_id and isinstance(p.position, GridPoint)
            }
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
            if (
                self.rules.gurps_equipment is not None
                and participant.posture == "prone"
                and posture == "standing"
            ):
                raise ValidationError("Rise from prone to kneeling before standing")
            if posture is None or any(
                value is not None for value in (destination, facing, item_id, target_id)
            ):
                raise ValidationError("Posture maneuver requires exactly one posture")
            participant = participant.model_copy(
                update={"posture": posture, "last_maneuver": maneuver}
            )
        elif maneuver in ATTACK_MANEUVERS:
            if (
                target_id is None
                or item_id is None
                or (
                    not deferred_step
                    and any(value is not None for value in (destination, facing, posture))
                )
            ):
                raise ValidationError("Attack intent requires a target and ready weapon")
            target = next((p for p in encounter.participants if p.actor_id == target_id), None)
            if (
                target is None
                or target.actor_id == actor_id
                or item_id not in participant.ready_item_ids
                or (
                    self.distance(participant.position, target.position) > participant.reach
                    and not any(
                        s.attacker_id == actor_id and s.defender_id == target_id
                        for s in encounter.ranged_situations
                    )
                )
            ):
                raise ValidationError("Attack target or weapon is unavailable or out of reach")
            participant = participant.model_copy(
                update={"last_maneuver": maneuver, "last_attack_item_id": item_id}
            )
            encounter = self._replace(encounter, participant)
            pending = PendingDefense(
                id=("defense:" + hashlib.sha256(command_id.encode()).hexdigest())
                if self.rules.gurps_equipment
                else f"{command_id}:defense",
                attacker_id=actor_id,
                defender_id=target_id,
                weapon_id=item_id,
                allowed=("dodge", "parry", "none") if target.ready_item_ids else ("dodge", "none"),
                opened_round=encounter.round,
                opened_turn=encounter.turn_index,
                post_attack_destination=destination if deferred_step else None,
                post_attack_square_facing=facing if deferred_step else None,
                post_attack_hex_path=hex_path if deferred_step else (),
                post_attack_facing=hex_facing if deferred_step else None,
                post_attack_posture=posture if deferred_step else None,
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
        elif maneuver in ("aim", "evaluate", "feint"):
            if (
                facing is not None
                or posture is not None
                or (maneuver == "evaluate" and item_id is not None)
            ):
                raise ValidationError("Unexpected observation maneuver parameters")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
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
        if (
            self.rules.gurps_equipment is None
            and not defender.reaction_available
            and selected != "none"
        ):
            raise ValidationError("Defender has no reaction available")
        defender = defender.model_copy(
            update={
                "reaction_available": False if selected != "none" else defender.reaction_available,
                "maneuver_state": defender.maneuver_state.model_copy(update={"defended": True})
                if selected != "none"
                else defender.maneuver_state,
            }
        )
        choice = DefenseChoice(pending=pending, selected=selected, chosen_by=actor_id)
        encounter = self._replace(encounter, defender).model_copy(
            update={
                "pending_defense": None,
                "defense_history": encounter.defense_history + (choice,),
            }
        )
        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        if (
            self.rules.gurps_equipment is not None
            and attacker.maneuver_state.attacks_remaining
            and not encounter.blocked_reason
        ):
            commitment = attacker.maneuver_state
            second_item = commitment.second_attack_item_id or pending.weapon_id
            second_target = commitment.second_attack_target_id or pending.defender_id
            if second_item not in attacker.ready_item_ids:
                commitment = commitment.model_copy(update={"attacks_remaining": 0})
                attacker = attacker.model_copy(update={"maneuver_state": commitment})
                encounter = self._replace(encounter, attacker)
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
            attacker = attacker.model_copy(
                update={
                    "maneuver_state": commitment.model_copy(
                        update={
                            "attacks_remaining": 0,
                            "attack_bonus": commitment.second_attack_penalty,
                        }
                    )
                }
            )
            encounter = self._replace(encounter, attacker).model_copy(
                update={
                    "pending_defense": pending.model_copy(
                        update={
                            "id": "second:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                            "weapon_id": second_item,
                            "defender_id": second_target,
                            "mode_id": commitment.second_attack_mode_id or pending.mode_id,
                            "post_attack_destination": None,
                            "post_attack_square_facing": None,
                            "post_attack_hex_path": (),
                            "post_attack_facing": None,
                            "post_attack_posture": None,
                        }
                    )
                }
            )
        else:
            if (
                pending.post_attack_destination is not None
                or pending.post_attack_square_facing is not None
            ):
                if not isinstance(attacker.position, GridPoint):
                    raise ValidationError("Square step requires square coordinates")
                occupied = {
                    p.position
                    for p in encounter.participants
                    if p.actor_id != attacker.actor_id and isinstance(p.position, GridPoint)
                }
                limit = max(1, (attacker.movement_allowance + 9) // 10)
                destination = pending.post_attack_destination or attacker.position
                if not self._reachable(
                    self.battlefields[encounter.battlefield_id],
                    attacker.position,
                    destination,
                    limit,
                    occupied,
                ):
                    raise ValidationError("Post-attack step is no longer available")
                attacker = attacker.model_copy(
                    update={
                        "position": destination,
                        "facing": pending.post_attack_square_facing or attacker.facing,
                    }
                )
                encounter = self._replace(encounter, attacker)
            if pending.post_attack_posture is not None:
                if {attacker.posture, pending.post_attack_posture} != {"standing", "kneeling"}:
                    raise ValidationError("Post-attack posture step is invalid")
                attacker = attacker.model_copy(update={"posture": pending.post_attack_posture})
                encounter = self._replace(encounter, attacker)
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
