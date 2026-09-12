"""Version-two vehicle dispatch under the existing transport transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import WaterOccupantCheck
from wayfarer.engine.rules.types.vehicle_capabilities import VEHICLE_OPERATIONS
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    durability,
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    UpgradeVehicle,
    VehicleCommand,
    VehicleControl,
    VehicleManeuver,
    VehicleRam,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    footprint,
)
from wayfarer.engine.simulation.movement.vehicles.operations import registry
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


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
    selected = registry.OPERATIONS.get(type(command), registry.collisions.resolve)
    state, affected = selected(
        registry.Operation(
            engine=engine,
            state=state,
            command=command,
            transport=t,
            board=board,
            health=health,
            occupied=occupied,
            rng=rng,
            operator=operator,
        )
    )
    changes = {v.id: v for v in affected}
    return state.model_copy(
        update={"transports": tuple(changes.get(v.id, v) for v in state.transports)}
    )
