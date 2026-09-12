"""Everything one vehicle operation may read, gathered once by the resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.engine.simulation.movement.vehicles.commands import VehicleCommand
from wayfarer.engine.simulation.resources import Pool, ResourceState

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


@dataclass(frozen=True, slots=True)
class Operation:
    """One vehicle command with the state, map and operator it acts on."""

    engine: ResourceEngine
    state: ResourceState
    command: VehicleCommand
    transport: Transport
    board: HexBattlefield | None
    health: dict[str, int] | None
    occupied: frozenset[Hex]
    rng: RandomSource
    operator: Pool


Outcome = tuple[ResourceState, tuple[Transport, ...]]
