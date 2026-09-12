"""Version-two vehicle dispatch under the existing transport transaction."""

from math import ceil

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec, require_hazards_settled
from wayfarer.rules.ranged_tables import range_penalty
from wayfarer.rules.recovery_types import require_settled
from wayfarer.rules.transport_types import Transport
from wayfarer.rules.vehicle_capabilities import VEHICLE_OPERATIONS
from wayfarer.rules.vehicle_types import VehicleTrace
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import ResourceEngine, ResourceState
from wayfarer.simulation.vehicle_collisions import (
    durability,
    impact,
    impact_actor,
    internal_id,
    roll_damage,
)
from wayfarer.simulation.vehicle_commands import (
    ResolveAirAftermath,
    ResolveVehicleEjection,
    UpgradeVehicle,
    VehicleControl,
    VehicleImpact,
    VehicleManeuver,
    VehicleRollover,
    VehicleSkid,
)
from wayfarer.simulation.vehicle_motion import control_vehicle, footprint, move_vehicle

VehicleCommand = (
    UpgradeVehicle
    | ResolveAirAftermath
    | VehicleControl
    | VehicleImpact
    | VehicleManeuver
    | VehicleRollover
    | VehicleSkid
    | ResolveVehicleEjection
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
    from wayfarer.simulation.transport import validate_transport

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
    if t.locomotion == "ground-mount":
        raise ValidationError("Version-two vehicle commands do not implement mounted combat")
    if command.kind not in VEHICLE_OPERATIONS[t.locomotion]:
        raise ValidationError("Vehicle operation requires an unsupported navigation adapter")
    durability(engine, state, t.body_id)
    occupied |= frozenset(
        cell
        for vehicle in state.transports
        if vehicle.id != t.id and vehicle.altitude == t.altitude
        for cell in footprint(vehicle)
    )
    operator = next(p for p in state.pools if p.id == "hp:" + t.operator_id)
    assert operator.injury is not None
    if isinstance(command, (VehicleManeuver, VehicleControl)) and (
        operator.injury.incapacitated or operator.injury.stunned
    ):
        raise ValidationError("Incapacitated operator cannot control a vehicle")
    affected: tuple[Transport, ...] = (t,)
    if isinstance(command, VehicleManeuver):
        if t.last_turn == state.game_time:
            raise ConflictError("Vehicle already moved this second")
        item = next(i for i in state.items if i.id == t.body_id)
        assert item.condition is not None
        if item.condition.disabled or item.condition.hp <= 0:
            raise ValidationError("Vehicle requires damage/stress resolution before movement")
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
        profile = engine.specs[item.definition_id].durability
        assert profile is not None
        affected = (
            move_vehicle(
                t,
                command,
                board,
                occupied | other_cells,
                rng,
                profile.ht,
                check_modifiers(state, t.operator_id, "dx"),
            ).model_copy(update={"last_turn": state.game_time}),
        )
    elif isinstance(command, VehicleControl):
        item = next(i for i in state.items if i.id == t.body_id)
        profile = engine.specs[item.definition_id].durability
        assert profile is not None
        if t.open_cabin and t.locomotion == "water":
            raise ValidationError("Open-deck control requires overboard and swimming consumers")
        recovering = t.locomotion == "air" and t.status in ("diving", "stalled")
        if recovering and t.aftermath_turn != state.game_time:
            raise ValidationError("Resolve this turn's air descent before recovery")
        if recovering and t.recovery_turn == state.game_time:
            raise ConflictError("Air recovery already attempted this second")
        controlled = control_vehicle(
            t, command, rng, profile.ht, check_modifiers(state, t.operator_id, "dx")
        )
        if recovering:
            controlled = controlled.model_copy(update={"recovery_turn": state.game_time})
        affected = (controlled,)
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
                from wayfarer.simulation.transport import collision_dice

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
