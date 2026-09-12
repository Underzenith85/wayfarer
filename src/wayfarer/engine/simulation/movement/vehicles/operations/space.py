"""Spacecraft navigation and delta-v."""

from fractions import Fraction
from math import ceil
from typing import Literal

from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    NavigateSpace,
)
from wayfarer.engine.simulation.movement.vehicles.operations.context import Operation, Outcome
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError


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
    state = operation.state
    command = operation.command
    assert isinstance(command, NavigateSpace)
    t = operation.transport
    board = operation.board
    occupied = operation.occupied
    affected: tuple[Transport, ...] = (t,)
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
            raise ValidationError("Coasting requires an authored scaled course at current speed")
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
    return state, affected
