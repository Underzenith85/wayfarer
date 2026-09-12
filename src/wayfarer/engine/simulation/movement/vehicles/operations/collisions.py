"""Skids, crashes and the collisions that follow."""

from typing import Literal

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec, require_hazards_settled
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.engine.simulation.movement.transport_validation import validate_transport
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    collision_dice,
    impact,
    impact_actor,
    internal_id,
    roll_damage,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    ResolveVehicleEjection,
    VehicleImpact,
    VehicleRollover,
    VehicleSkid,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    footprint,
)
from wayfarer.engine.simulation.movement.vehicles.operations.context import Operation, Outcome
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


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


def resolve(operation: Operation) -> Outcome:

    engine = operation.engine
    state = operation.state
    command = operation.command
    t = operation.transport
    board = operation.board
    health = operation.health
    occupied = operation.occupied
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
    if health is None:
        raise ValidationError("Collision requires compiled occupant HT")
    if isinstance(command, VehicleSkid):
        if not t.locomotion.startswith("ground-") or t.status != "skidding":
            raise ValidationError("Vehicle has no pending ground skid")
        if board is None or board.profile_id != t.profile_id:
            raise ValidationError("Skid requires matching map")
        target = next((v for v in state.transports if v.id == command.target_transport_id), None)
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
        pending = next((e for e in t.pending_ejections if e.actor_id == command.passenger_id), None)
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
                    "status": "crashed" if len(t.pending_ejections) == 1 else "ejection-pending",
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
                    raise ValidationError("Rollover hits an actor; resolve the collision target")
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
        # The branches above consumed every other collision command.
        assert isinstance(command, VehicleImpact)
        assert health is not None
        target = next((v for v in state.transports if v.id == command.target_transport_id), None)
        if command.target_transport_id is not None and target is None:
            raise ValidationError("Unknown collision transport target")
        if target is not None:
            validate_transport(engine, state, target)
            actors = frozenset((*target.occupants, target.body_id))
            require_settled(state.recovery_tasks, actors, state.game_time)
            require_hazards_settled(state.hazards, actors, state.game_time)
        state, affected = impact(engine, state, command, t, target, health, rng)
    return state, affected
