"""Which operation resolves which vehicle command.

Each operation reads the same ``Operation`` record and returns the state it
produced with the transports it changed, so the resolver's job is to gather the
context once and look the command up rather than to walk a ladder of types.
"""

from collections.abc import Callable
from typing import Final

from wayfarer.engine.simulation.movement.vehicles import commands
from wayfarer.engine.simulation.movement.vehicles.operations import (
    air,
    collisions,
    control,
    damage,
    maneuver,
    mounts,
    ram,
    space,
    water,
)
from wayfarer.engine.simulation.movement.vehicles.operations.context import Operation, Outcome

__all__ = ["OPERATIONS", "Operation", "Outcome", "collisions"]

OPERATIONS: Final[dict[type[commands.VehicleCommand], Callable[[Operation], Outcome]]] = {
    commands.VehicleManeuver: maneuver.resolve,
    commands.VehicleControl: control.resolve,
    commands.VehicleRam: ram.resolve,
    commands.DamageVehicle: damage.resolve,
    commands.NavigateSpace: space.resolve,
    commands.ResolveMountSeparation: mounts.resolve,
    commands.ResolveAirAftermath: air.resolve,
    commands.ResolveWaterAftermath: water.resolve,
}
