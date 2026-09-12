"""Driving one vehicle through its move."""

from typing import Literal

from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import WaterOccupantCheck
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import impaired_movement
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    VehicleManeuver,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    footprint,
    move_vehicle,
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
    assert isinstance(command, VehicleManeuver)
    t = operation.transport
    board = operation.board
    occupied = operation.occupied
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
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
    return state, affected
