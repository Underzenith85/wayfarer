"""The combat engine: encounter lifecycle, legality and the action economy.

Explicit combat for the supported prototype subset. Attack intent persists a
defense-choice pause; configured original prototype profiles resolve checks,
protection and injury atomically when that choice resumes.
"""

from __future__ import annotations

import hashlib
import json
from math import sqrt
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    CombatResult,
    DefenseChoice,
    Encounter,
    PendingDefense,
    basic_distance,
    basic_reachable,
    basic_visible,
    move_basic,
)
from wayfarer.engine.simulation.combat.maneuvers import (
    ATTACK_MANEUVERS,
    AttackOption,
    DefenseOption,
    ManeuverState,
    WaitInterrupt,
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
)
from wayfarer.engine.simulation.combat.suppression import (
    ActiveSuppressionZone,
    PendingSuppressionAttack,
)
from wayfarer.engine.simulation.combat.vocabulary import Defense, Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, HexFacing
from wayfarer.engine.simulation.resources import Equip, ResourceEngine, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.commands import BasicMove


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
            from wayfarer.engine.simulation.hex_geometry import distance

            return distance(left, right)
        if not isinstance(left, GridPoint) or not isinstance(right, GridPoint):
            raise ValidationError("Coordinate systems require explicit migration")
        return abs(left.x - right.x) + abs(left.y - right.y)

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
            or encounter.status == "active"
            and len(participants) < 2
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
            for p in sorted(encounter.participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        if encounter.turn_order != expected_order:
            raise ValidationError("Turn order does not match initiative")
        if encounter.spatial_kind == "hex":
            from wayfarer.engine.simulation.combat.tactical import validate_hex_encounter

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
        from wayfarer.engine.simulation.combat.unarmed_records import validate_control

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
                from wayfarer.engine.simulation.magic.spells import active_spells

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
            p.actor_id for p in sorted(participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        encounter = Encounter(
            darkness_penalty=self.battlefields[battlefield_id].darkness_penalty,
            id=encounter_id,
            spatial_context=spatial_context,
            participants=participants,
            turn_order=order,
        )
        self.validate(encounter, world, resources, actor_ids)
        return encounter

    def start_basic(
        self,
        encounter_id: str,
        participant_ids: tuple[str, ...],
        facts: tuple[BasicSpatialFact, ...],
        initiatives: dict[str, int],
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> Encounter:
        participants = tuple(
            Combatant(
                actor_id=actor_id,
                initiative=initiatives[actor_id],
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
            p.actor_id for p in sorted(participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        encounter = Encounter(
            id=encounter_id,
            spatial_context=BasicSpatialContext(facts=facts),
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
            spatial_revision=spatial_revision or original_resources.revision + 1,
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
            basic_move=basic_move,
            suppression_fire=suppression_fire,
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
                        original.spatial_kind != "basic"
                        and action == "attack"
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
                    if original.spatial_kind == "hex":
                        from wayfarer.engine.simulation.combat.tactical import sight

                        observable = sight(
                            result[0], waiter, after_actor, board=self.hex_map(result[0])
                        )
                    elif original.spatial_kind == "basic":
                        observable = basic_visible(original, waiter_id, actor_id)
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
                    moved = (
                        basic_move is not None
                        if original.spatial_kind == "basic"
                        else self.distance(before_actor.position, after_actor.position) >= 1
                    )
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
                        if original.spatial_kind == "basic":
                            paused = paused.model_copy(
                                update={"spatial_context": result[0].spatial}
                            )
                        else:
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
                                "basic_move": None,
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
        spatial_revision: int,
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
        hex_facing: HexFacing | None = None,
        basic_move: BasicMove | None = None,
        suppression_fire: bool = False,
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
        start_position = (
            None if isinstance(encounter.spatial, BasicSpatialContext) else participant.position
        )
        # B205: a stream lasts only while its holder keeps pouring it on the same
        # weapon and mode. Any other maneuver lets go of it (#359).
        if participant.stream is not None and not (
            maneuver in ATTACK_MANEUVERS and item_id == participant.stream.weapon_id
        ):
            participant = participant.model_copy(update={"stream": None})
            encounter = self._replace(encounter, participant)
        basic = isinstance(encounter.spatial, BasicSpatialContext)
        battlefield = None if basic else self.battlefields[encounter.battlefield_id]
        deferred_step = step_timing == "after"
        if deferred_step and maneuver != "attack":
            raise ValidationError("Only Attack permits a step after the attack")
        if deferred_step and not (
            destination is not None
            or hex_path
            or hex_facing is not None
            or posture is not None
            or basic_move is not None
        ):
            raise ValidationError("A post-attack step requires movement, facing, or posture")
        if deferred_step and encounter.spatial_kind == "hex":
            from wayfarer.engine.simulation.combat.tactical import move_hex

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
                move_hex(
                    encounter,
                    participant,
                    "attack",
                    hex_path,
                    hex_facing,
                    None,
                    board=self.hex_map(encounter),
                )
        elif deferred_step and destination is not None:
            assert battlefield is not None
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
        if encounter.spatial_kind == "hex" and not deferred_step:
            from wayfarer.engine.simulation.combat.tactical import move_hex

            if destination is not None or facing is not None:
                raise ValidationError("Hex encounters require explicit hex paths and facings")
            if posture is not None and hex_path:
                raise ValidationError("A posture step cannot also translate the actor")
            participant = move_hex(
                encounter,
                participant,
                maneuver,
                hex_path,
                hex_facing,
                defense_option,
                board=self.hex_map(encounter),
            )
            encounter = self._replace(encounter, participant)
        elif (hex_path or hex_facing is not None) and not deferred_step:
            raise ValidationError("Hex movement requires explicit battlefield migration")
        if basic_move is not None:
            if not basic or destination is not None or facing is not None or hex_path or hex_facing:
                raise ValidationError("Basic movement cannot mix mapped movement fields")
            allowed_steps = {
                "attack",
                "aim",
                "evaluate",
                "feint",
                "ready",
                "concentrate",
                "all_out_defense",
            }
            if maneuver != "move" and maneuver not in allowed_steps:
                raise ValidationError("Maneuver does not permit basic movement")
            if maneuver in ("attack", "aim", "evaluate", "feint"):
                raise ValidationError(
                    "Basic movement with a spatially dependent maneuver requires "
                    "post-movement GM adjudication"
                )
            if not deferred_step:
                allowance = (
                    self.rules.prone_movement_allowance
                    if maneuver == "move" and participant.posture == "prone"
                    else participant.movement_allowance
                    if maneuver == "move"
                    else max(1, (participant.movement_allowance + 9) // 10)
                )
                encounter = move_basic(
                    encounter,
                    actor_id=actor_id,
                    reference_actor_id=basic_move.reference_actor_id,
                    direction=basic_move.direction,
                    yards=allowance,
                    command_id=command_id,
                    revision=spatial_revision,
                )
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
                from wayfarer.engine.simulation.magic.spells import active_spells

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
                    if encounter.spatial_kind != "hex":
                        raise ValidationError("Wait zones require an explicit hex battlefield")
                    cells = {
                        (cell.position.q, cell.position.r)
                        for cell in self.require_hex(encounter).cells
                    }
                    if not set(wait_trigger.zone) <= cells:
                        raise ValidationError("Wait zone is outside the battlefield")
                if wait_trigger.stop_thrust:
                    if basic:
                        raise ValidationError("Basic stop thrust requires explicit GM adjudication")
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
                    and (
                        basic_distance(encounter, participant.actor_id, target.actor_id)
                        if basic
                        else self.distance(participant.position, target.position)
                    )
                    > reach
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
                assert battlefield is not None
                if not self._reachable(
                    battlefield,
                    participant.position,
                    destination,
                    limit,
                    occupied,
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
        if maneuver == "move" and encounter.spatial_kind == "hex":
            if any(value is not None for value in (posture, item_id, target_id)):
                raise ValidationError("Move accepts only a path and facing")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        elif maneuver == "move" and basic:
            if basic_move is None or any(
                value is not None for value in (destination, facing, posture, item_id, target_id)
            ):
                raise ValidationError("Basic Move requires one authoritative relative movement")
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
                not isinstance(battlefield, Battlefield)
                or destination.x >= battlefield.width
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
            # A bound actor may spend its Ready struggling with the binding
            # instead of readying an item (#354).
            struggling = participant.entangled is not None
            if any(value is not None for value in (destination, facing, posture, target_id)) or (
                item_id is None and not struggling
            ):
                raise ValidationError("Ready requires exactly one item")
            if item_id is not None:
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
        elif maneuver in ATTACK_MANEUVERS and suppression_fire:
            if (
                maneuver != "all_out_attack"
                or item_id is None
                or target_id is not None
                or item_id not in participant.ready_item_ids
                or deferred_step
                or any(value is not None for value in (destination, facing, posture))
            ):
                raise ValidationError("Suppression fire requires an immobile All-Out Attack")
            participant = participant.model_copy(
                update={"last_maneuver": maneuver, "last_attack_item_id": item_id}
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
                    not basic_reachable(encounter, actor_id, target_id)
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
                post_attack_basic_reference_id=(
                    basic_move.reference_actor_id if deferred_step and basic_move else None
                ),
                post_attack_basic_direction=(
                    basic_move.direction if deferred_step and basic_move else None
                ),
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
        encounter = self._replace(encounter, participant)
        movement_path: tuple[Hex, ...] = ()
        if isinstance(start_position, Hex):
            after = next(p for p in encounter.participants if p.actor_id == actor_id).position
            movement_path = hex_path or (
                (after,) if isinstance(after, Hex) and after != start_position else ()
            )
        if isinstance(start_position, Hex) and movement_path:
            encounter = self._suppression_attacks(
                encounter,
                actor_id=actor_id,
                before=start_position,
                path=movement_path,
                command_id=command_id,
            )
        if encounter.pending_defense is not None:
            pending = encounter.pending_defense
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
        encounter = self._advance(encounter)
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
        suppression_queue = tuple(
            attack
            for attack in pending.suppression_attacks
            if any(
                zone.id == attack.zone_id and zone.remaining_hits > 0
                for zone in encounter.suppression_zones
            )
        )
        if (
            self.rules.gurps_equipment is not None
            and pending.suppression_zone_id is not None
            and suppression_queue
            and not encounter.blocked_reason
        ):
            suppression_following, *suppression_remaining = suppression_queue
            encounter = encounter.model_copy(
                update={
                    "pending_defense": pending.model_copy(
                        update={
                            "id": "suppression:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                            "attacker_id": suppression_following.attacker_id,
                            "weapon_id": suppression_following.weapon_id,
                            "mode_id": suppression_following.mode_id,
                            "shots": suppression_following.shots,
                            "spray_targets": (),
                            "spray_recoil_penalty": 0,
                            "traversal_shots": 0,
                            "suppression_zone_id": suppression_following.zone_id,
                            "suppression_attacks": tuple(suppression_remaining),
                            "suppression_remaining_hits": suppression_following.remaining_hits,
                            "suppression_aim_bonus": suppression_following.aim_bonus,
                            "suppression_skill_cap": suppression_following.skill_cap,
                        }
                    )
                }
            )
        elif (
            self.rules.gurps_equipment is not None
            and pending.spray_targets
            and not encounter.blocked_reason
        ):
            spray_following, *spray_remaining = pending.spray_targets
            encounter = encounter.model_copy(
                update={
                    "pending_defense": pending.model_copy(
                        update={
                            "id": "spray:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                            "defender_id": spray_following.target_id,
                            "shots": spray_following.shots,
                            "hit_location": spray_following.hit_location,
                            "target_item_id": None,
                            "spray_targets": tuple(spray_remaining),
                            "spray_recoil_penalty": spray_following.recoil_penalty,
                            "traversal_shots": spray_following.traversal_shots,
                            "post_attack_destination": None,
                            "post_attack_square_facing": None,
                            "post_attack_hex_path": (),
                            "post_attack_facing": None,
                            "post_attack_posture": None,
                        }
                    )
                }
            )
        elif (
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
