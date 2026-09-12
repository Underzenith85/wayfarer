"""The combat engine: encounter lifecycle, legality and the action economy.

Explicit combat for the supported prototype subset. Attack intent persists a
defense-choice pause; configured original prototype profiles resolve checks,
protection and injury atomically when that choice resumes.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from math import sqrt
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.defense import choose_defense
from wayfarer.engine.simulation.combat.encounter import (
    CombatAllegiance,
    Combatant,
    CombatResult,
    Encounter,
    PendingDefense,
    SideOpposition,
)
from wayfarer.engine.simulation.combat.maneuvers import (
    AttackOption,
    CrouchAction,
    DefenseOption,
    WaitTrigger,
)
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    BasicSpatialFact,
    DistanceSpatialFact,
    HexActorPlacement,
    HexSpatialContext,
    Placement,
    ReachSpatialFact,
    SpatialContext,
    SquareActorPlacement,
    SquareSpatialContext,
    point_distance,
)
from wayfarer.engine.simulation.combat.suppression import (
    ActiveSuppressionZone,
    PendingSuppressionAttack,
)
from wayfarer.engine.simulation.combat.tactical import validate_hex_encounter
from wayfarer.engine.simulation.combat.turns import apply_turn, take_turn
from wayfarer.engine.simulation.combat.unarmed.records import validate_control
from wayfarer.engine.simulation.combat.vocabulary import Defense, Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, HexFacing
from wayfarer.engine.simulation.magic.spells import active_spells
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.commands import BasicMove
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


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
        return point_distance(left, right)

    @staticmethod
    def _inside_suppression(point: Hex, zone: ActiveSuppressionZone) -> bool:
        """Whether a hex center lies in the two-yard zone or its one-yard swath."""

        def xy(value: Hex) -> tuple[float, float]:
            return value.q + value.r / 2, value.r * sqrt(3) / 2

        px, py = xy(point)
        ax, ay = xy(zone.origin)
        bx, by = xy(zone.center)
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return (px - bx) ** 2 + (py - by) ** 2 <= 1
        ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        closest_x, closest_y = ax + ratio * dx, ay + ratio * dy
        return (px - closest_x) ** 2 + (py - closest_y) ** 2 <= 1

    def _suppression_attacks(
        self,
        encounter: Encounter,
        *,
        actor_id: str,
        before: Hex,
        path: tuple[Hex, ...],
        command_id: str,
    ) -> Encounter:
        attacks: list[PendingSuppressionAttack] = []
        zones: list[ActiveSuppressionZone] = []
        for zone in encounter.suppression_zones:
            enters = (
                zone.attacker_id != actor_id
                and actor_id not in zone.attacked_actor_ids
                and zone.remaining_hits > 0
                and not self._inside_suppression(before, zone)
                and any(self._inside_suppression(point, zone) for point in path)
            )
            if enters:
                attacks.append(
                    PendingSuppressionAttack(
                        zone_id=zone.id,
                        attacker_id=zone.attacker_id,
                        weapon_id=zone.weapon_id,
                        mode_id=zone.mode_id,
                        shots=zone.shots,
                        remaining_hits=zone.remaining_hits,
                        aim_bonus=zone.aim_bonus,
                        skill_cap=zone.skill_cap,
                    )
                )
                zone = zone.model_copy(
                    update={"attacked_actor_ids": zone.attacked_actor_ids + (actor_id,)}
                )
            zones.append(zone)
        if not attacks:
            return encounter
        first, *remaining = attacks
        target = next(p for p in encounter.participants if p.actor_id == actor_id)
        pending = PendingDefense(
            id="suppression:" + hashlib.sha256(command_id.encode()).hexdigest(),
            attacker_id=first.attacker_id,
            defender_id=actor_id,
            weapon_id=first.weapon_id,
            mode_id=first.mode_id,
            shots=first.shots,
            allowed=("dodge", "parry", "none") if target.ready_item_ids else ("dodge", "none"),
            opened_round=encounter.round,
            opened_turn=encounter.turn_index,
            hit_location="random",
            suppression_zone_id=first.zone_id,
            suppression_attacks=tuple(remaining),
            suppression_remaining_hits=first.remaining_hits,
            suppression_aim_bonus=first.aim_bonus,
            suppression_skill_cap=first.skill_cap,
            interrupted_actor_id=actor_id,
        )
        return encounter.model_copy(
            update={"pending_defense": pending, "suppression_zones": tuple(zones)}
        )

    def validate(
        self,
        encounter: Encounter,
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> None:
        spatial = encounter.spatial
        battlefield = (
            None
            if isinstance(spatial, BasicSpatialContext)
            else self.battlefields.get(spatial.battlefield_id)
        )
        if not isinstance(spatial, BasicSpatialContext) and battlefield is None:
            raise ValidationError("Encounter battlefield is not configured")
        entities = {entity.id: entity for entity in world.entities}
        participants = {p.actor_id: p for p in encounter.participants}
        placements = (
            {}
            if isinstance(spatial, BasicSpatialContext)
            else {p.actor_id: p for p in spatial.placements}
        )
        if (
            len(participants) != len(encounter.participants)
            or not 1 <= len(participants) <= self.rules.max_combatants
            or set(participants) != set(encounter.turn_order)
            or len(set(encounter.turn_order)) != len(encounter.turn_order)
            or encounter.turn_index >= len(encounter.turn_order)
            or not set(participants) <= actor_ids
            or not isinstance(spatial, BasicSpatialContext)
            and (set(placements) != set(participants) or len(placements) != len(spatial.placements))
        ):
            raise ValidationError("Invalid encounter participants or turn order")
        expected_order = tuple(
            p.actor_id
            for p in sorted(
                encounter.participants,
                key=lambda p: (-p.initiative, -p.initiative_dx, p.actor_id),
            )
        )
        if encounter.turn_order != expected_order:
            raise ValidationError("Turn order does not match initiative")
        if encounter.spatial_kind == "hex":
            validate_hex_encounter(
                encounter, self.rules.gurps_equipment, board=self.hex_map(encounter)
            )
        if isinstance(spatial, BasicSpatialContext):
            for fact in spatial.facts:
                if (
                    fact.subject_id == fact.object_id
                    or fact.subject_id not in participants
                    or fact.object_id not in participants
                ):
                    raise ValidationError("Basic spatial facts must relate encounter participants")
            for actor_id, actor in participants.items():
                for other_id in participants:
                    if actor_id == other_id:
                        continue
                    reach = spatial.active("reach", actor_id, other_id)
                    distance = spatial.active("distance", actor_id, other_id)
                    if isinstance(reach, ReachSpatialFact) and isinstance(
                        distance, DistanceSpatialFact
                    ):
                        expected = (
                            "close"
                            if distance.yards == 0
                            else "reachable"
                            if distance.yards <= actor.reach
                            else "separated"
                        )
                        if reach.relation != expected:
                            raise ValidationError("Basic distance and reach facts conflict")
        occupied: set[GridPoint | Hex] = set()
        blocked = set(battlefield.blocked) if isinstance(battlefield, Battlefield) else set()
        if battlefield is not None:
            self.hex_map(encounter)
        ready = {
            (item.owner_id, item.id) for item in resources.items if item.equipped and item.ready
        }
        for participant in encounter.participants:
            entity = entities.get(participant.actor_id)
            if entity is None or entity.kind is not EntityKind.ACTOR:
                raise ValidationError("Combatant is not at the battlefield location")
            if not isinstance(spatial, BasicSpatialContext):
                assert battlefield is not None
                placement = placements[participant.actor_id]
                if encounter.status == "active" and entity.location_id != battlefield.location_id:
                    raise ValidationError("Combatant is not at the battlefield location")
                if (
                    (
                        isinstance(placement.position, GridPoint)
                        and (
                            not isinstance(battlefield, Battlefield)
                            or (
                                placement.position.x >= battlefield.width
                                or placement.position.y >= battlefield.height
                            )
                        )
                    )
                    or (encounter.spatial_kind != "hex" and isinstance(placement.position, Hex))
                    or placement.position in blocked
                    or (
                        placement.position in occupied
                        and not (
                            self.rules.gurps_equipment is not None
                            and self.rules.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
                            and all(
                                tuple(sorted((other.actor_id, participant.actor_id)))
                                in encounter.close_pairs
                                for other in encounter.participants
                                if other.actor_id != participant.actor_id
                                and placements[other.actor_id].position == placement.position
                            )
                        )
                    )
                ):
                    raise ValidationError(
                        "Combatant position is blocked, occupied or out of bounds"
                    )
                occupied.add(placement.position)
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

        validate_control(
            encounter,
            resources,
            basic=(
                self.rules.gurps_equipment is not None
                and self.rules.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
            ),
        )
        suppression_ids: set[str] = set()
        for zone in encounter.suppression_zones:
            attacker = participants.get(zone.attacker_id)
            if (
                encounter.spatial_kind != "hex"
                or zone.id in suppression_ids
                or attacker is None
                or not isinstance(zone.origin, Hex)
                or zone.weapon_id not in attacker.ready_item_ids
                or zone.remaining_hits > zone.shots
                or zone.attacker_id in zone.attacked_actor_ids
                or len(set(zone.attacked_actor_ids)) != len(zone.attacked_actor_ids)
                or not set(zone.attacked_actor_ids) <= set(participants)
            ):
                raise ValidationError("Invalid active suppression zone")
            assert isinstance(battlefield, HexBattlefield)
            if battlefield.cell(zone.origin).blocked or battlefield.cell(zone.center).blocked:
                raise ValidationError("Invalid active suppression-zone geometry")
            suppression_ids.add(zone.id)
        pending = encounter.pending_defense
        if pending is not None:
            suppression_pause = pending.suppression_zone_id is not None
            spell_weapon = False
            if pending.spell_cast_id is not None:
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
                or (
                    pending.attacker_id != encounter.current_actor_id
                    and not (
                        suppression_pause
                        and pending.interrupted_actor_id == encounter.current_actor_id
                        and pending.defender_id == encounter.current_actor_id
                    )
                )
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
            queued_suppression_ids = (
                (pending.suppression_zone_id,) if pending.suppression_zone_id is not None else ()
            ) + tuple(attack.zone_id for attack in pending.suppression_attacks)
            if suppression_pause and (
                pending.interrupted_actor_id != pending.defender_id
                or pending.suppression_remaining_hits < 1
                or pending.suppression_skill_cap is None
                or len(set(queued_suppression_ids)) != len(queued_suppression_ids)
                or not set(queued_suppression_ids) <= suppression_ids
            ):
                raise ValidationError("Invalid pending suppression attack")
            if not suppression_pause and (
                pending.suppression_attacks
                or pending.suppression_remaining_hits
                or pending.suppression_skill_cap is not None
                or pending.interrupted_actor_id is not None
            ):
                raise ValidationError("Ordinary defense contains suppression state")
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

    def require_hex(self, encounter: Encounter) -> HexBattlefield:
        board = self.hex_map(encounter)
        if board is None:
            raise ValidationError("Encounter requires a hex template")
        return board

    def hex_map(self, encounter: Encounter) -> HexBattlefield | None:
        return hex_template(encounter, self.rules)

    def start(
        self,
        encounter_id: str,
        battlefield_id: str,
        placements: tuple[Placement, ...],
        initiatives: dict[str, Decimal | int],
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
        *,
        dexterities: dict[str, int] | None = None,
        allegiances: tuple[CombatAllegiance, ...] = (),
        oppositions: tuple[SideOpposition, ...] = (),
        automatic_completion: bool = False,
        reinforcements_expected: bool = False,
    ) -> Encounter:
        participants = tuple(
            Combatant(
                actor_id=p.actor_id,
                initiative=initiatives[p.actor_id],
                initiative_dx=(dexterities or {}).get(p.actor_id, 0),
                position=p.position,
                facing=p.facing,
                hex_facing=p.hex_facing,
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
        board = self.battlefields[battlefield_id]
        if isinstance(board, HexBattlefield):
            if any(not isinstance(p.position, Hex) or p.hex_facing is None for p in placements):
                raise ValidationError("Hex encounter requires hex positions and facings")
            spatial_context: SpatialContext = HexSpatialContext(
                battlefield_id=battlefield_id,
                placements=tuple(
                    HexActorPlacement(actor_id=p.actor_id, position=p.position, facing=p.hex_facing)
                    for p in placements
                    if isinstance(p.position, Hex) and p.hex_facing is not None
                ),
            )
        else:
            if any(
                not isinstance(p.position, GridPoint) or p.hex_facing is not None
                for p in placements
            ):
                raise ValidationError("Square encounter requires square positions and facings")
            spatial_context = SquareSpatialContext(
                battlefield_id=battlefield_id,
                placements=tuple(
                    SquareActorPlacement(actor_id=p.actor_id, position=p.position, facing=p.facing)
                    for p in placements
                    if isinstance(p.position, GridPoint)
                ),
            )
        order = tuple(
            p.actor_id
            for p in sorted(
                participants, key=lambda p: (-p.initiative, -p.initiative_dx, p.actor_id)
            )
        )
        encounter = Encounter(
            darkness_penalty=self.battlefields[battlefield_id].darkness_penalty,
            id=encounter_id,
            spatial_context=spatial_context,
            participants=participants,
            turn_order=order,
            allegiances=allegiances,
            oppositions=oppositions,
            completion_policy=(
                "automatic"
                if automatic_completion
                else "gm"
                if allegiances or oppositions or reinforcements_expected
                else "legacy"
            ),
            reinforcements_expected=reinforcements_expected,
        )
        self.validate(encounter, world, resources, actor_ids)
        return encounter

    def start_basic(
        self,
        encounter_id: str,
        participant_ids: tuple[str, ...],
        facts: tuple[BasicSpatialFact, ...],
        initiatives: dict[str, Decimal | int],
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
        *,
        dexterities: dict[str, int] | None = None,
        allegiances: tuple[CombatAllegiance, ...] = (),
        oppositions: tuple[SideOpposition, ...] = (),
        automatic_completion: bool = False,
        reinforcements_expected: bool = False,
    ) -> Encounter:
        participants = tuple(
            Combatant(
                actor_id=actor_id,
                initiative=initiatives[actor_id],
                initiative_dx=(dexterities or {}).get(actor_id, 0),
                reach=self.rules.default_reach,
                movement_allowance=self.rules.movement_allowance,
                ready_item_ids=tuple(
                    sorted(
                        item.id
                        for item in resources.items
                        if item.owner_id == actor_id and item.equipped and item.ready
                    )
                ),
            )
            for actor_id in participant_ids
        )
        order = tuple(
            p.actor_id
            for p in sorted(
                participants, key=lambda p: (-p.initiative, -p.initiative_dx, p.actor_id)
            )
        )
        encounter = Encounter(
            id=encounter_id,
            spatial_context=BasicSpatialContext(facts=facts),
            participants=participants,
            turn_order=order,
            allegiances=allegiances,
            oppositions=oppositions,
            completion_policy=(
                "automatic"
                if automatic_completion
                else "gm"
                if allegiances or oppositions or reinforcements_expected
                else "legacy"
            ),
            reinforcements_expected=reinforcements_expected,
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
        updated = encounter.model_copy(
            update={
                "participants": tuple(
                    participant if p.actor_id == participant.actor_id else p
                    for p in encounter.participants
                )
            }
        )
        context = updated.spatial
        if isinstance(context, SquareSpatialContext):
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square context cannot contain hex coordinates")
            return updated.replace_placement(
                SquareActorPlacement(
                    actor_id=participant.actor_id,
                    position=participant.position,
                    facing=participant.facing,
                )
            )
        if isinstance(context, HexSpatialContext):
            if not isinstance(participant.position, Hex) or participant.hex_facing is None:
                raise ValidationError("Hex context requires hex coordinates and facing")
            return updated.replace_placement(
                HexActorPlacement(
                    actor_id=participant.actor_id,
                    position=participant.position,
                    facing=participant.hex_facing,
                )
            )
        return updated

    @staticmethod
    def _reachable(
        battlefield: Battlefield | HexBattlefield,
        start: GridPoint,
        destination: GridPoint,
        limit: int,
        occupied: set[GridPoint],
    ) -> bool:
        if not isinstance(battlefield, Battlefield):
            raise ValidationError("Square movement requires a square template")
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
                "suppression_zones": tuple(
                    zone
                    for zone in encounter.suppression_zones
                    if zone.attacker_id != encounter.turn_order[index]
                ),
                "participants": tuple(
                    p.model_copy(
                        update={
                            "reaction_available": True,
                            "parries": (),
                            "block_used": False,
                            "defense_penalty": 0,
                            "unarmed_balance_lost": False,
                            "unarmed_guard_dropped": False,
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
        crouch: CrouchAction | None = None,
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
        hex_facing: HexFacing | None = None,
        basic_move: BasicMove | None = None,
        spatial_revision: int | None = None,
        suppression_fire: bool = False,
    ) -> tuple[Encounter, ResourceState, CombatResult]:

        return take_turn(
            self,
            encounter,
            actor_id=actor_id,
            maneuver=maneuver,
            resources=resources,
            destination=destination,
            facing=facing,
            posture=posture,
            crouch=crouch,
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
            command_json=command_json,
            hex_path=hex_path,
            hex_facing=hex_facing,
            basic_move=basic_move,
            spatial_revision=spatial_revision,
            suppression_fire=suppression_fire,
        )

    def _take_turn(
        self,
        encounter: Encounter,
        *,
        actor_id: str,
        maneuver: Maneuver,
        resources: ResourceState,
        spatial_revision: int,
        command_id: str,
        destination: GridPoint | None = None,
        facing: Facing | None = None,
        posture: Posture | None = None,
        crouch: CrouchAction | None = None,
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
        hex_facing: HexFacing | None = None,
        basic_move: BasicMove | None = None,
        suppression_fire: bool = False,
    ) -> tuple[Encounter, ResourceState, CombatResult]:

        return apply_turn(
            self,
            encounter,
            actor_id=actor_id,
            maneuver=maneuver,
            resources=resources,
            spatial_revision=spatial_revision,
            command_id=command_id,
            destination=destination,
            facing=facing,
            posture=posture,
            crouch=crouch,
            item_id=item_id,
            target_id=target_id,
            attack_option=attack_option,
            defense_option=defense_option,
            wait_trigger=wait_trigger,
            step_timing=step_timing,
            second_item_id=second_item_id,
            second_target_id=second_target_id,
            second_mode_id=second_mode_id,
            hex_path=hex_path,
            hex_facing=hex_facing,
            basic_move=basic_move,
            suppression_fire=suppression_fire,
        )

    def choose_defense(
        self, encounter: Encounter, *, actor_id: str, selected: Defense
    ) -> tuple[Encounter, CombatResult]:

        return choose_defense(self, encounter, actor_id=actor_id, selected=selected)


def validate_consequences(rules: CombatRules, state: PlayState) -> None:
    """Authored consequences must name known battlefields, approved actors and facts."""
    actor_ids = {a.actor_id for a in state.actors}
    facts = {f.id for f in state.world.facts}
    fields = {b.id for b in rules.battlefields}
    if len({c.id for c in rules.consequences}) != len(rules.consequences):
        raise ValidationError("Duplicate combat consequence")
    if any(
        c.battlefield_id not in fields
        or c.defeated_actor_id not in actor_ids
        or not set(c.recipient_actor_ids) <= actor_ids
        or not set(c.fact_ids) <= facts
        for c in rules.consequences
    ):
        raise ValidationError("Invalid combat consequence references")


def hex_template(encounter: Encounter, rules: CombatRules | None) -> HexBattlefield | None:
    if rules is None:
        raise ValidationError("Encounter requires combat rules")
    if isinstance(encounter.spatial, BasicSpatialContext):
        return None
    template = next((b for b in rules.battlefields if b.id == encounter.battlefield_id), None)
    if template is None:
        raise ValidationError("Encounter battlefield is not configured")
    if (encounter.spatial_kind == "hex") != isinstance(template, HexBattlefield):
        raise ValidationError("Encounter geometry disagrees with its template")
    return template if isinstance(template, HexBattlefield) else None
