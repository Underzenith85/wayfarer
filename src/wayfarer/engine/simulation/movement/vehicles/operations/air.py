"""What follows an aircraft leaving the air."""

from math import ceil
from typing import Literal

from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import WaterOccupantCheck
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    impact,
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    ResolveAirAftermath,
    VehicleImpact,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    footprint,
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
    engine = operation.engine
    state = operation.state
    command = operation.command
    assert isinstance(command, ResolveAirAftermath)
    t = operation.transport
    board = operation.board
    health = operation.health
    occupied = operation.occupied
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
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
            return state, affected
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
    return state, affected
