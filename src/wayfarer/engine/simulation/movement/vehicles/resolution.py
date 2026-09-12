"""Version-two vehicle dispatch under the existing transport transaction."""

from decimal import Decimal
from fractions import Fraction
from math import ceil
from typing import Literal

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.physical import falling_damage
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec, require_hazards_settled
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.rules.types.vehicle_capabilities import VEHICLE_OPERATIONS
from wayfarer.engine.simulation.equipment.objects import DamageObject, StressObject, apply_object
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury, impaired_movement
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, distance, neighbor
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    durability,
    impact,
    impact_actor,
    internal_id,
    roll_damage,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    DamageVehicle,
    NavigateSpace,
    ResolveAirAftermath,
    ResolveMountSeparation,
    ResolveVehicleEjection,
    ResolveWaterAftermath,
    UpgradeVehicle,
    VehicleControl,
    VehicleImpact,
    VehicleManeuver,
    VehicleRam,
    VehicleRollover,
    VehicleSkid,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    control_vehicle,
    footprint,
    move_vehicle,
)
from wayfarer.engine.simulation.resources import ResourceEngine, ResourceState
from wayfarer.errors import ConflictError, ValidationError

VehicleCommand = (
    UpgradeVehicle
    | ResolveAirAftermath
    | ResolveWaterAftermath
    | NavigateSpace
    | ResolveMountSeparation
    | VehicleRam
    | DamageVehicle
    | VehicleControl
    | VehicleImpact
    | VehicleManeuver
    | VehicleRollover
    | VehicleSkid
    | ResolveVehicleEjection
)


def water_hazard(
    command_id: str,
    state: ResourceState,
    board: HexBattlefield,
    facts: WaterOccupantCheck,
    kind: Literal["drowning", "suffocation", "pressure"],
) -> HazardSchedule:
    """Bind a water casualty to the shared environmental clock."""
    stage: Literal["struggling", "cycles", "exposure"]
    if kind == "drowning":
        interval, cycles, damage, resistible, stage = 5, 100000, 0, True, "struggling"
    elif kind == "suffocation":
        interval, cycles, damage, resistible, stage = 1, 240, 0, False, "cycles"
    elif kind == "pressure":
        interval, cycles, damage, resistible, stage = 1, 100000, 1, True, "exposure"
    else:
        raise ValidationError("Unknown water casualty")
    return HazardSchedule(
        id=internal_id(command_id, kind + ":" + facts.actor_id),
        actor_id=facts.actor_id,
        spec=HazardSpec(
            id="vehicle-" + kind,
            kind=kind,
            scene_id=board.id,
            delay=1 if kind == "suffocation" else 0,
            interval=interval,
            cycles=cycles,
            damage_dice=damage,
            damage_add=0 if damage else 1,
            resistible=resistible,
            reference="Basic Set Campaigns B435-437, B469",
        ),
        started=state.game_time,
        due=state.game_time + interval,
        remaining=cycles,
        ht=facts.ht,
        will=facts.will,
        swimming=facts.swimming,
        stage=stage,
        no_air_since=state.game_time if kind == "suffocation" else None,
        next_check_at=state.game_time + 5 if kind == "drowning" else None,
    )


def resolve_vehicle(
    engine: ResourceEngine,
    state: ResourceState,
    command: VehicleCommand,
    t: Transport,
    *,
    board: HexBattlefield | None,
    health: dict[str, int] | None,
    occupied: frozenset[Hex],
    rng: RandomSource,
) -> ResourceState:
    from wayfarer.engine.simulation.movement.transport import validate_transport

    if isinstance(command, UpgradeVehicle):
        if (
            t.mechanics_version != 1
            or t.locomotion != "ground-wheeled"
            or t.speed
            or t.status != "controlled"
        ):
            raise ValidationError("Upgrade requires a stopped, controlled version-one vehicle")
        t = t.model_copy(update={"mechanics_version": 2})
        return state.model_copy(
            update={"transports": tuple(t if v.id == t.id else v for v in state.transports)}
        )
    if t.mechanics_version != 2:
        raise ValidationError("Vehicle command requires explicit transport version 2 migration")
    if command.kind not in VEHICLE_OPERATIONS[t.locomotion]:
        raise ValidationError("Vehicle operation requires an unsupported navigation adapter")
    if isinstance(command, (VehicleManeuver, VehicleControl, VehicleRam)) and set(
        t.disabled_systems
    ) & {
        "hull",
        "motive",
        "controls",
    }:
        raise ValidationError("Vehicle disabled system prevents operation")
    if t.locomotion != "ground-mount":
        durability(engine, state, t.body_id)
    occupied |= frozenset(
        cell
        for vehicle in state.transports
        if vehicle.id != t.id and vehicle.altitude == t.altitude
        for cell in footprint(vehicle)
    )
    operator = next(p for p in state.pools if p.id == "hp:" + t.operator_id)
    assert operator.injury is not None
    if (
        isinstance(command, (VehicleManeuver, VehicleControl, VehicleRam))
        and t.operator_id not in t.occupants
    ):
        raise ValidationError("Resolve separated-operator consequences before vehicle control")
    if isinstance(command, (VehicleManeuver, VehicleControl, VehicleRam)) and (
        operator.injury.incapacitated or operator.injury.stunned
    ):
        raise ValidationError("Incapacitated operator cannot control a vehicle")
    affected: tuple[Transport, ...] = (t,)
    if isinstance(command, VehicleManeuver):
        if t.last_turn == state.game_time:
            raise ConflictError("Vehicle already moved this second")
        item = next((i for i in state.items if i.id == t.body_id), None)
        if t.locomotion == "ground-mount":
            mount_pool = next(p for p in state.pools if p.id == "hp:" + t.body_id)
            if command.end_speed > impaired_movement(mount_pool, t.acceleration):
                raise ValidationError("Mount speed exceeds its current ordinary movement")
            profile_ht = 10
        else:
            assert item is not None and item.condition is not None
            if item.condition.disabled or item.condition.hp <= 0:
                raise ValidationError("Vehicle requires damage/stress resolution before movement")
            profile = engine.specs[item.definition_id].durability
            assert profile is not None
            profile_ht = profile.ht
        if t.subhex_thirds:
            raise ValidationError("Fractional skid endpoint requires tactical pose reconciliation")
        if board is None:
            raise ValidationError("Vehicle movement requires an authored map")
        other_cells = frozenset(
            c
            for v in state.transports
            if v.id != t.id and v.altitude == t.altitude
            for c in footprint(v)
        )
        affected = (
            move_vehicle(
                t,
                command,
                board,
                occupied | other_cells,
                rng,
                profile_ht,
                check_modifiers(state, t.operator_id, "dx"),
            ).model_copy(update={"last_turn": state.game_time}),
        )
    elif isinstance(command, VehicleControl):
        profile_ht = 10
        if t.locomotion != "ground-mount":
            item = next(i for i in state.items if i.id == t.body_id)
            profile = engine.specs[item.definition_id].durability
            assert profile is not None
            profile_ht = profile.ht
        deck = t.open_cabin and t.locomotion == "water"
        if deck and (
            board is None
            or board.profile_id != t.profile_id
            or {f.actor_id for f in command.water_occupants} != set(t.occupants)
        ):
            raise ValidationError("Open-deck control requires compiled checks and a water map")
        if not deck and command.water_occupants:
            raise ValidationError("Open-deck checks only apply to exposed watercraft occupants")
        recovering = t.locomotion == "air" and t.status in ("diving", "stalled")
        if recovering and t.aftermath_turn != state.game_time:
            raise ValidationError("Resolve this turn's air descent before recovery")
        if recovering and t.recovery_turn == state.game_time:
            raise ConflictError("Air recovery already attempted this second")
        controlled = control_vehicle(
            t, command, rng, profile_ht, check_modifiers(state, t.operator_id, "dx")
        )
        if recovering:
            controlled = controlled.model_copy(update={"recovery_turn": state.game_time})
        if deck and controlled.control_margin is not None and controlled.control_margin < 0:
            assert board is not None
            severe = controlled.status in ("capsized", "sinking")
            tossed = tuple(
                facts.actor_id
                for facts in command.water_occupants
                if severe
                or not success_roll(
                    t.profile_id,
                    facts.hold_skill,
                    check_modifiers(state, facts.actor_id, "st"),
                    rng=rng,
                ).outcome.succeeded
            )
            if tossed:
                facts_by_actor = {facts.actor_id: facts for facts in command.water_occupants}
                state = state.model_copy(
                    update={
                        "hazards": (
                            *state.hazards,
                            *(
                                water_hazard(
                                    command.id, state, board, facts_by_actor[a], "drowning"
                                )
                                for a in tossed
                            ),
                        )
                    }
                )
                controlled = controlled.model_copy(
                    update={
                        "occupants": tuple(a for a in controlled.occupants if a not in tossed),
                        "overboard": (*controlled.overboard, *tossed),
                    }
                )
        affected = (controlled,)
    elif isinstance(command, VehicleRam):
        target = next(
            (vehicle for vehicle in state.transports if vehicle.id == command.target_transport_id),
            None,
        )
        if (
            t.locomotion == "ground-mount"
            or target is None
            or target.id == t.id
            or target.mechanics_version != 2
            or target.locomotion == "ground-mount"
        ):
            raise ValidationError("Ramming requires two distinct version-two vehicles")
        durability(engine, state, target.body_id)
        if (
            t.speed == 0
            or t.altitude != target.altitude
            or distance(Hex(q=t.q, r=t.r), Hex(q=target.q, r=target.r)) > 1
        ):
            raise ValidationError("Declared ram requires an adjacent target at the same altitude")
        if (command.defense == "dodge") != (command.defender_skill is not None):
            raise ValidationError("Ram Dodge requires exactly one compiled defense value")
        target_operator = next(p for p in state.pools if p.id == "hp:" + target.operator_id)
        assert target_operator.injury is not None
        if command.defender_skill is not None and (
            target.operator_id not in target.occupants
            or target_operator.injury.incapacitated
            or target_operator.injury.stunned
        ):
            raise ValidationError("Ram Dodge requires an able target operator")
        attack = success_roll(
            t.profile_id,
            max(1, command.skill + t.handling + t.attack_penalty),
            check_modifiers(state, t.operator_id, "dx"),
            rng=rng,
        )
        ram_traces = [
            VehicleTrace(
                command_id=command.id,
                reason="declared-ram-attack",
                actor_id=t.operator_id,
                dice=attack.dice,
                target=attack.effective_target,
                margin=attack.margin,
            )
        ]
        defended = False
        if attack.outcome.succeeded and command.defender_skill is not None:
            defense = success_roll(
                target.profile_id,
                max(1, command.defender_skill + target.handling),
                check_modifiers(state, target.operator_id, "dx"),
                rng=rng,
            )
            defended = defense.outcome.succeeded
            ram_traces.append(
                VehicleTrace(
                    command_id=command.id,
                    reason="declared-ram-defense",
                    actor_id=target.operator_id,
                    dice=defense.dice,
                    target=defense.effective_target,
                    margin=defense.margin,
                )
            )
        t = t.model_copy(
            update={
                "attack_penalty": 0,
                "aim_lost": False,
                "traces": (*t.traces, *ram_traces),
            }
        )
        if not attack.outcome.succeeded or defended:
            affected = (t,)
        else:
            if health is None:
                raise ValidationError("Ram collision requires compiled occupant HT")
            collision = VehicleImpact(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=command.expected_revision,
                transport_id=t.id,
                target_transport_id=target.id,
                angle=command.angle,
                speed_after=command.speed_after,
                target_speed_after=command.target_speed_after,
                protection=command.protection,
            )
            state, affected = impact(engine, state, collision, t, target, health, rng)
    elif isinstance(command, DamageVehicle):
        if t.locomotion == "ground-mount":
            raise ValidationError("Mounted creatures use the existing injury reducer")
        if (command.hit_location == "weapon") != (command.equipment_item_id is not None):
            raise ValidationError("Weapon hits require exactly one vehicle equipment item")
        operator_facts = command.operator_damage > 0 and command.operator_ht is not None
        if (command.operator_damage > 0) != (command.operator_ht is not None):
            raise ValidationError("Control hits require matching compiled operator injury facts")
        if command.hit_location != "controls" and operator_facts:
            raise ValidationError("Only control hits may include operator injury")
        item_id = command.equipment_item_id or t.body_id
        if command.equipment_item_id is not None:
            equipment = next((item for item in state.items if item.id == item_id), None)
            if equipment is None or equipment.owner_id not in t.occupants:
                raise ValidationError("Vehicle weapon must belong to one of its occupants")
        state, object_result = apply_object(
            engine,
            state,
            DamageObject(
                id=internal_id(command.id, "vehicle-hit"),
                actor_id=command.actor_id,
                expected_revision=state.revision,
                item_id=item_id,
                basic_damage=command.basic_damage,
                damage_type=command.damage_type,
            ),
            system=True,
            rng=rng,
        )
        if (
            item_id == t.body_id
            and object_result.condition.hp <= 0
            and not object_result.condition.disabled
        ):
            state, object_result = apply_object(
                engine,
                state,
                StressObject(
                    id=internal_id(command.id, "vehicle-stress"),
                    actor_id=command.actor_id,
                    expected_revision=state.revision,
                    item_id=item_id,
                ),
                system=True,
                rng=rng,
            )
        if command.operator_damage:
            assert command.operator_ht is not None
            state, _ = apply_injury(
                state,
                Wound(
                    id=internal_id(command.id, "operator"),
                    actor_id=t.operator_id,
                    expected_revision=state.revision,
                    basic_damage=command.operator_damage,
                    resistance=0,
                    damage_type="cr",
                    injury_source="area",
                ),
                ht=command.operator_ht,
                rng=rng,
                system=True,
            )
        disabled = list(t.disabled_systems)
        if object_result.injury and command.hit_location != "hull":
            disabled.append(command.hit_location)
        if item_id == t.body_id and object_result.condition.disabled:
            disabled.append("hull")
        disabled = list(dict.fromkeys(disabled))
        affected = (
            t.model_copy(
                update={
                    "disabled_systems": tuple(disabled),
                    "status": "crashed"
                    if "hull" in disabled
                    else "control-required"
                    if command.hit_location in ("motive", "controls") and object_result.injury
                    else t.status,
                    "stress_turn": state.game_time
                    if item_id == t.body_id and object_result.condition.hp <= 0
                    else t.stress_turn,
                    "traces": (
                        *t.traces,
                        VehicleTrace(
                            command_id=command.id,
                            reason="vehicle-hit-" + command.hit_location,
                            actor_id=item_id,
                            basic_damage=command.basic_damage,
                            injury=object_result.injury,
                            dice=tuple(d for roll in object_result.checks for d in roll),
                        ),
                    ),
                }
            ),
        )
    elif isinstance(command, NavigateSpace):
        if t.locomotion != "space" or t.status not in ("controlled", "drifting"):
            raise ValidationError("Space navigation requires an operational spacecraft")
        if t.last_turn == state.game_time:
            raise ConflictError("Spacecraft already navigated this second")
        delta_pool = next((p for p in state.pools if p.id == "delta-v:" + t.body_id), None)
        if (
            delta_pool is None
            or delta_pool.injury is not None
            or delta_pool.fatigue is not None
            or delta_pool.maximum != t.top_speed
        ):
            raise ValidationError("Spacecraft requires a dedicated delta-v resource pool")
        if command.action == "burn":
            if command.course or command.miles_per_hex or command.target_speed == t.speed:
                raise ValidationError("A burn changes velocity without borrowing tactical movement")
            delta = abs(command.target_speed - t.speed)
            if delta > delta_pool.current:
                raise ValidationError("Insufficient delta-v for the declared burn")
            elapsed = ceil(Fraction(delta, t.space_acceleration_tenths_g))
            state = state.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": p.current - delta})
                        if p.id == delta_pool.id
                        else p
                        for p in state.pools
                    )
                }
            )
            affected = (
                t.model_copy(
                    update={
                        "speed": command.target_speed,
                        "status": "controlled",
                        "last_turn": state.game_time,
                        "space_elapsed_seconds": t.space_elapsed_seconds + elapsed,
                        "traces": (
                            *t.traces,
                            VehicleTrace(
                                command_id=command.id,
                                reason="space-burn",
                                actor_id=t.body_id,
                                resource_spent=delta,
                                elapsed_seconds=elapsed,
                                target=command.target_speed,
                            ),
                        ),
                    }
                ),
            )
        else:
            if (
                board is None
                or board.profile_id != t.profile_id
                or not command.course
                or not command.miles_per_hex
                or command.target_speed != t.speed
                or not t.speed
            ):
                raise ValidationError(
                    "Coasting requires an authored scaled course at current speed"
                )
            point = Hex(q=t.q, r=t.r)
            for direction in command.course:
                point = neighbor(point, direction)
                if board.cell(point).blocked or point in occupied:
                    raise ValidationError("Space course requires collision resolution")
            distance_miles = len(command.course) * command.miles_per_hex
            elapsed = ceil(Fraction(1800 * distance_miles, t.speed))
            affected = (
                t.model_copy(
                    update={
                        "q": point.q,
                        "r": point.r,
                        "status": "controlled",
                        "last_turn": state.game_time,
                        "space_elapsed_seconds": t.space_elapsed_seconds + elapsed,
                        "traces": (
                            *t.traces,
                            VehicleTrace(
                                command_id=command.id,
                                reason="space-coast",
                                actor_id=t.body_id,
                                distance_miles=distance_miles,
                                elapsed_seconds=elapsed,
                            ),
                        ),
                    }
                ),
            )
    elif isinstance(command, ResolveMountSeparation):
        mounted_collision = bool(command.collision_speed)
        if t.locomotion != "ground-mount" or (
            not mounted_collision and t.status not in ("rider-separated", "mount-fallen")
        ):
            raise ValidationError("Mount has no pending rider separation")
        if mounted_collision and t.status != "controlled":
            raise ValidationError("Mounted collision requires a controlled, attached pair")
        if health is None or any(a not in health for a in (t.operator_id, t.body_id)):
            raise ValidationError("Mount separation requires compiled rider and mount HT")
        rider_yards = t.rider_fall_yards
        riding_trace: VehicleTrace | None = None
        if t.status == "mount-fallen":
            if command.riding_skill is None:
                raise ValidationError("A falling mount requires the rider's compiled Riding skill")
            riding = success_roll(
                t.profile_id,
                max(1, command.riding_skill - 2 + t.mount_riding_penalty),
                check_modifiers(state, t.operator_id, "dx"),
                rng=rng,
            )
            rider_yards = 2 if riding.outcome.succeeded else 3
            riding_trace = VehicleTrace(
                command_id=command.id,
                reason="mount-fall-riding",
                actor_id=t.operator_id,
                dice=riding.dice,
                target=riding.effective_target,
                margin=riding.margin,
            )
        elif command.riding_skill is not None:
            raise ValidationError("Riding follow-up only applies when the mount falls")
        from wayfarer.engine.simulation.movement.transport import collision_dice

        traces: list[VehicleTrace] = []
        for actor, yards in (
            (t.operator_id, rider_yards),
            (t.body_id, t.mount_fall_yards),
        ):
            if not yards and not mounted_collision:
                continue
            pool = next(p for p in state.pools if p.id == "hp:" + actor)
            damage, dice = roll_damage(
                collision_dice(pool.maximum, command.collision_speed, hard=True)
                if mounted_collision
                else falling_damage(pool.maximum, Decimal(yards)),
                rng,
            )
            state, result = apply_injury(
                state,
                Wound(
                    id=internal_id(command.id, "fall:" + actor),
                    actor_id=actor,
                    expected_revision=state.revision,
                    basic_damage=damage,
                    resistance=0,
                    damage_type="cr",
                    injury_source="area",
                ),
                ht=health[actor],
                rng=rng,
                system=True,
            )
            traces.append(
                VehicleTrace(
                    command_id=command.id,
                    reason="mount-separation-collision"
                    if mounted_collision
                    else "mount-separation-fall",
                    actor_id=actor,
                    dice=dice,
                    basic_damage=damage,
                    injury=result.injury,
                )
            )
        affected = (
            t.model_copy(
                update={
                    "occupants": (),
                    "status": "crashed",
                    "speed": 0,
                    "rider_fall_yards": 0,
                    "mount_fall_yards": 0,
                    "traces": (
                        *t.traces,
                        *((riding_trace,) if riding_trace is not None else ()),
                        *traces,
                    ),
                }
            ),
        )
    elif isinstance(command, ResolveAirAftermath):
        if t.locomotion != "air" or t.status not in ("drifting", "diving", "stalled"):
            raise ValidationError("Aircraft has no pending motion aftermath")
        if t.aftermath_turn == state.game_time:
            raise ConflictError("Air aftermath already resolved this second")
        if board is None or board.profile_id != t.profile_id:
            raise ValidationError("Air aftermath requires the matching tactical map")
        point = Hex(q=t.q, r=t.r)
        altitude = t.altitude
        vertical_speed = t.vertical_speed
        status = t.status
        if status == "drifting":
            for _ in range(t.remaining_points):
                destination = neighbor(point, t.facing)
                pose = t.model_copy(update={"q": destination.q, "r": destination.r})
                if any(
                    altitude <= board.cell(cell).ground + board.cell(cell).opaque_height
                    or cell in occupied
                    for cell in footprint(pose)
                ):
                    break
                point = destination
            else:
                affected = (
                    t.model_copy(
                        update={
                            "q": point.q,
                            "r": point.r,
                            "remaining_points": 0,
                            "status": "controlled",
                            "aftermath_turn": state.game_time,
                        }
                    ),
                )
                changes = {v.id: v for v in affected}
                return state.model_copy(
                    update={"transports": tuple(changes.get(v.id, v) for v in state.transports)}
                )
            vertical_speed = max(1, t.speed)
        elif status == "diving":
            vertical_speed = t.top_speed
            altitude -= vertical_speed
        else:
            next_speed = min(t.top_speed, vertical_speed + 10)
            altitude -= (vertical_speed + next_speed) // 2
            vertical_speed = next_speed
        surface_pose = t.model_copy(update={"q": point.q, "r": point.r})
        surface = ceil(
            max(
                board.cell(cell).ground + board.cell(cell).opaque_height
                for cell in footprint(surface_pose)
            )
        )
        crashed = altitude <= surface or status == "drifting"
        updated_t = t.model_copy(
            update={
                "q": point.q,
                "r": point.r,
                "altitude": max(surface, altitude),
                "vertical_speed": vertical_speed,
                "aftermath_turn": state.game_time,
                "speed": max(t.speed, vertical_speed) if crashed else t.speed,
            }
        )
        if crashed:
            if health is None:
                raise ValidationError("Air crash requires compiled occupant HT")
            collision = VehicleImpact(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=command.expected_revision,
                transport_id=t.id,
                angle="immovable",
                protection=command.protection,
            )
            state, affected = impact(engine, state, collision, updated_t, None, health, rng)
            affected = tuple(
                v.model_copy(update={"status": "crashed"}) if v.status != "ejection-pending" else v
                for v in affected
            )
        else:
            affected = (updated_t,)
    elif isinstance(command, ResolveWaterAftermath):
        if t.locomotion not in ("water", "underwater"):
            raise ValidationError("Water aftermath requires a water vehicle")
        if board is None or board.profile_id != t.profile_id:
            raise ValidationError("Water aftermath requires the matching tactical map")
        if command.action == "drift":
            if (
                t.status != "drifting"
                or command.direction is None
                or not command.distance
                or command.waterline is None
                or command.skill is not None
                or command.leak_damage
            ):
                raise ValidationError("Current drift requires direction, distance, and waterline")
            point = Hex(q=t.q, r=t.r)
            for _ in range(command.distance):
                point = neighbor(point, command.direction)
                pose = t.model_copy(update={"q": point.q, "r": point.r})
                for cell in footprint(pose):
                    terrain = board.cell(cell)
                    if cell in occupied:
                        raise ValidationError("Water current meets an undeclared collision")
                    if t.locomotion == "water":
                        if command.waterline - terrain.ground < Fraction(
                            t.draft * 12 + t.draft_inches, 12
                        ):
                            raise ValidationError("Water current grounds the vehicle")
                    elif not terrain.ground < t.altitude < command.waterline:
                        raise ValidationError("Underwater current meets surface or bottom")
            affected = (
                t.model_copy(
                    update={
                        "q": point.q,
                        "r": point.r,
                        "status": "controlled",
                        "aftermath_turn": state.game_time,
                    }
                ),
            )
        elif command.action == "right":
            if (
                t.status != "capsized"
                or not t.unsinkable
                or t.operator_id not in t.occupants
                or command.skill is None
                or command.direction is not None
                or command.distance
                or command.leak_damage
            ):
                raise ValidationError("Only an unsinkable capsized craft can be righted")
            if t.recovery_turn == state.game_time:
                raise ConflictError("Capsize recovery already attempted this second")
            check = success_roll(
                t.profile_id,
                command.skill + t.handling,
                check_modifiers(state, command.actor_id, "dx"),
                rng=rng,
            )
            affected = (
                t.model_copy(
                    update={
                        "status": "controlled" if check.outcome.succeeded else "capsized",
                        "recovery_turn": state.game_time,
                        "traces": (
                            *t.traces,
                            VehicleTrace(
                                command_id=command.id,
                                reason="capsize-recovery",
                                actor_id=command.actor_id,
                                dice=check.dice,
                                target=check.effective_target,
                                margin=check.margin,
                            ),
                        ),
                    }
                ),
            )
        elif command.action == "sink":
            if t.status != "sinking" or command.waterline is None or command.skill is not None:
                raise ValidationError("Only a sinking craft accepts a sinking tick")
            if t.aftermath_turn == state.game_time:
                raise ConflictError("Sinking already advanced this second")
            facts = {f.actor_id: f for f in command.occupants}
            if set(facts) != set(t.occupants):
                raise ValidationError("Sinking requires compiled facts for every occupant")
            submersion = t.submersion + t.sink_rate + t.leak_rate
            bottom = min(board.cell(cell).ground for cell in footprint(t))
            depth = command.waterline - bottom
            kind: Literal["drowning", "suffocation"] = "drowning" if t.open_cabin else "suffocation"
            existing = {h.actor_id for h in state.hazards if h.active}
            state = state.model_copy(
                update={
                    "hazards": (
                        *state.hazards,
                        *(
                            water_hazard(command.id, state, board, facts[a], kind)
                            for a in t.occupants
                            if a not in existing
                        ),
                    )
                }
            )
            affected = (
                t.model_copy(
                    update={
                        "submersion": submersion,
                        "status": "crashed" if submersion >= depth else "sinking",
                        "aftermath_turn": state.game_time,
                    }
                ),
            )
        else:
            if (
                command.action != "stress-leak"
                or t.locomotion != "underwater"
                or t.status != "stress-failure"
                or not command.leak_damage
                or command.skill is not None
            ):
                raise ValidationError("Underwater stress requires an authored leak outcome")
            facts = {f.actor_id: f for f in command.occupants}
            if set(facts) != set(t.occupants):
                raise ValidationError("Pressure loss requires compiled occupant facts")
            state, _ = apply_object(
                engine,
                state,
                DamageObject(
                    id=internal_id(command.id, "hull"),
                    actor_id=command.actor_id,
                    expected_revision=state.revision,
                    item_id=t.body_id,
                    basic_damage=command.leak_damage,
                    damage_type="cr",
                ),
                system=True,
                rng=rng,
            )
            existing = {h.actor_id for h in state.hazards if h.active}
            state = state.model_copy(
                update={
                    "hazards": (
                        *state.hazards,
                        *(
                            water_hazard(command.id, state, board, facts[a], "pressure")
                            for a in t.occupants
                            if a not in existing
                        ),
                    )
                }
            )
            affected = (
                t.model_copy(
                    update={"status": "sinking", "leak_rate": max(1, command.leak_damage)}
                ),
            )
    else:
        if health is None:
            raise ValidationError("Collision requires compiled occupant HT")
        if isinstance(command, VehicleSkid):
            if not t.locomotion.startswith("ground-") or t.status != "skidding":
                raise ValidationError("Vehicle has no pending ground skid")
            if board is None or board.profile_id != t.profile_id:
                raise ValidationError("Skid requires matching map")
            target = next(
                (v for v in state.transports if v.id == command.target_transport_id), None
            )
            if (target is None) != (command.target_transport_id is None):
                raise ValidationError("Unknown skid collision target")
            if (target is not None and command.impact_angle is None) or (
                target is None and command.impact_angle is not None
            ):
                raise ValidationError("Transport skid collision requires an explicit target angle")
            if command.target_transport_id is not None and command.target_actor_id is not None:
                raise ValidationError("Skid collision accepts one declared target")
            actor_point = None
            if command.target_actor_id is not None:
                if (
                    command.target_actor_id not in engine.actors
                    or command.target_actor_id in t.occupants
                    or command.target_q is None
                    or command.target_r is None
                ):
                    raise ValidationError("Skid actor target requires an authored tactical pose")
                actor_point = Hex(q=command.target_q, r=command.target_r)
                if actor_point not in occupied:
                    raise ValidationError("Skid actor target is not in the occupied map facts")
            elif command.target_q is not None or command.target_r is not None:
                raise ValidationError("Skid target pose requires an actor target")
            target_cells = footprint(target) if target is not None else frozenset()
            point = Hex(q=t.q, r=t.r)
            elevation = board.cell(point).ground
            elevation_extra = board.cell(point).extra_cost
            hit = False
            hit_target = False
            hit_actor = False
            remaining = t.remaining_points
            while remaining:
                destination = neighbor(point, t.facing)
                pose = t.model_copy(update={"q": destination.q, "r": destination.r})
                surcharge = 0
                for cell in footprint(pose):
                    terrain = board.cell(cell)
                    if cell in occupied:
                        if target is not None and cell in target_cells:
                            hit_target = True
                            continue
                        if actor_point is not None and cell == actor_point:
                            hit_actor = True
                            continue
                        raise ValidationError("Skid hits an undeclared collision target")
                    if terrain.ground != elevation and not (terrain.extra_cost or elevation_extra):
                        raise ValidationError("Skid slope requires an authored movement surcharge")
                    surcharge = max(surcharge, terrain.extra_cost)
                    hit |= terrain.blocked
                if hit or hit_target or hit_actor:
                    break
                step_cost = 1 + surcharge
                if step_cost > remaining:
                    break
                remaining -= step_cost
                point = destination
                elevation = board.cell(point).ground
                elevation_extra = board.cell(point).extra_cost
            updated_t = t.model_copy(
                update={
                    "q": point.q,
                    "r": point.r,
                    "remaining_points": remaining,
                    "status": "controlled" if not remaining else "skidding",
                }
            )
            if hit_actor:
                assert command.target_actor_id is not None
                state, struck = impact_actor(
                    engine,
                    state,
                    command,
                    updated_t,
                    command.target_actor_id,
                    health,
                    rng,
                )
                affected = (struck,)
            elif hit or hit_target:
                collision = VehicleImpact(
                    id=command.id,
                    actor_id=command.actor_id,
                    expected_revision=command.expected_revision,
                    transport_id=t.id,
                    angle=command.impact_angle or "immovable",
                    target_transport_id=target.id if hit_target and target else None,
                    speed_after=command.speed_after,
                    target_speed_after=command.target_speed_after,
                    protection=command.protection,
                )
                assert health is not None
                state, affected = impact(
                    engine,
                    state,
                    collision,
                    updated_t,
                    target if hit_target else None,
                    health,
                    rng,
                )
            else:
                affected = (updated_t,)
        elif isinstance(command, ResolveVehicleEjection):
            if board is None or board.profile_id != t.profile_id:
                raise ValidationError("Ejection placement requires the matching tactical map")
            pending = next(
                (e for e in t.pending_ejections if e.actor_id == command.passenger_id), None
            )
            if pending is None:
                raise ValidationError("Passenger has no pending ejection")
            point = Hex(q=pending.origin_q, r=pending.origin_r)
            for _ in range(pending.distance_yards):
                point = neighbor(point, pending.facing)
                if point in occupied:
                    raise ValidationError("Ejection path requires a declared follow-on impact")
                if board.cell(point).blocked:
                    break
            if (point.q, point.r) != (command.destination_q, command.destination_r):
                raise ValidationError("Ejection destination disagrees with collision knockback")
            if command.landing == "water" and command.swimming_skill is None:
                raise ValidationError("Water ejection requires compiled Swimming skill")
            if command.landing != "water" and command.swimming_skill is not None:
                raise ValidationError("Swimming skill only applies to a water landing")
            actor_ht = health.get(command.passenger_id)
            if type(actor_ht) is not int or actor_ht < 1:
                raise ValidationError("Ejection resolution requires compiled passenger HT")
            pool = next(p for p in state.pools if p.id == "hp:" + command.passenger_id)
            clean_water_entry = False
            if command.landing == "water":
                assert command.swimming_skill is not None
                clean_water_entry = success_roll(
                    t.profile_id,
                    max(1, command.swimming_skill + range_penalty(pending.collision_speed)),
                    check_modifiers(state, command.passenger_id, "ht"),
                    rng=rng,
                ).outcome.succeeded
                drowning = HazardSchedule(
                    id=internal_id(command.id, "drowning:" + command.passenger_id),
                    actor_id=command.passenger_id,
                    spec=HazardSpec(
                        id="vehicle-ejection-water",
                        kind="drowning",
                        scene_id=board.id,
                        interval=5,
                        cycles=1000,
                        reference="B354/B431/B436",
                    ),
                    started=state.game_time,
                    due=state.game_time + (300 if clean_water_entry else 5),
                    remaining=1000,
                    ht=actor_ht,
                    will=actor_ht,
                    swimming=command.swimming_skill,
                    full_hp=pool.maximum,
                    stage="swimming" if clean_water_entry else "struggling",
                    next_check_at=state.game_time + (300 if clean_water_entry else 5),
                )
                state = state.model_copy(update={"hazards": (*state.hazards, drowning)})
            if command.landing != "water" or not clean_water_entry:
                from wayfarer.engine.simulation.movement.transport import collision_dice

                damage, _ = roll_damage(
                    collision_dice(
                        pool.maximum,
                        pending.collision_speed,
                        hard=command.landing == "hard",
                    ),
                    rng,
                )
                state, _ = apply_injury(
                    state,
                    Wound(
                        id=internal_id(command.id, "landing:" + command.passenger_id),
                        actor_id=command.passenger_id,
                        expected_revision=state.revision,
                        basic_damage=damage,
                        resistance=0,
                        damage_type="cr",
                        injury_source="area",
                    ),
                    ht=actor_ht,
                    rng=rng,
                    system=True,
                )
            trace = VehicleTrace(
                command_id=command.id,
                reason="ejection-water" if command.landing == "water" else "ejection-landed",
                actor_id=command.passenger_id,
                ejection_yards=pending.distance_yards,
                destination_q=point.q,
                destination_r=point.r,
            )
            affected = (
                t.model_copy(
                    update={
                        "pending_ejections": tuple(
                            e for e in t.pending_ejections if e.actor_id != command.passenger_id
                        ),
                        "status": "crashed"
                        if len(t.pending_ejections) == 1
                        else "ejection-pending",
                        "traces": (*t.traces, trace),
                    }
                ),
            )
        elif isinstance(command, VehicleRollover):
            assert health is not None
            if not t.locomotion.startswith("ground-") or t.status != "crashed" or not t.skid_thirds:
                raise ValidationError("Vehicle has no pending ground rollover")
            if board is None or board.profile_id != t.profile_id:
                raise ValidationError("Rollover requires the matching hex map")
            point = Hex(q=t.q, r=t.r)
            initial_height = board.cell(point).ground
            hit = False
            for _ in range(t.skid_thirds // 3):
                destination = neighbor(point, t.facing)
                pose = t.model_copy(update={"q": destination.q, "r": destination.r})
                for cell in footprint(pose):
                    terrain = board.cell(cell)
                    if cell in occupied:
                        raise ValidationError(
                            "Rollover hits an actor; resolve the collision target"
                        )
                    if terrain.ground != initial_height or terrain.extra_cost:
                        raise ValidationError("Rollover requires slope or surface resolution")
                    hit |= terrain.blocked
                if hit:
                    break
                point = destination
            updated_t = t.model_copy(
                update={
                    "q": point.q,
                    "r": point.r,
                    "subhex_thirds": 0 if hit else t.skid_thirds % 3,
                    "skid_thirds": 0,
                }
            )
            collision = VehicleImpact(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=command.expected_revision,
                transport_id=t.id,
                angle="immovable",
                protection=command.protection,
            )
            state, affected = impact(engine, state, collision, updated_t, None, health, rng)
            affected = tuple(
                v.model_copy(update={"status": "crashed"}) if v.status != "ejection-pending" else v
                for v in affected
            )
        else:
            assert health is not None
            target = next(
                (v for v in state.transports if v.id == command.target_transport_id), None
            )
            if command.target_transport_id is not None and target is None:
                raise ValidationError("Unknown collision transport target")
            if target is not None:
                validate_transport(engine, state, target)
                actors = frozenset((*target.occupants, target.body_id))
                require_settled(state.recovery_tasks, actors, state.game_time)
                require_hazards_settled(state.hazards, actors, state.game_time)
            state, affected = impact(engine, state, command, t, target, health, rng)
    changes = {v.id: v for v in affected}
    return state.model_copy(
        update={"transports": tuple(changes.get(v.id, v) for v in state.transports)}
    )
