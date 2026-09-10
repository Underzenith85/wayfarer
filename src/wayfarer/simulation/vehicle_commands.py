"""Internal resolved vehicle commands. These are not part of frozen scenario/play v1."""

from typing import Literal

from pydantic import Field

from wayfarer.rules.vehicle_types import PassengerProtection
from wayfarer.simulation.hex_geometry import HexFacing
from wayfarer.simulation.resources import Command


class VehicleManeuver(Command):
    kind: Literal["vehicle-maneuver"] = "vehicle-maneuver"
    transport_id: str
    course: tuple[HexFacing, ...] = Field(max_length=100)
    end_speed: int = Field(ge=0, le=100)
    waterline: int | None = None
    control_skill: int | None = Field(default=None, ge=1, le=50)


class VehicleControl(Command):
    kind: Literal["vehicle-control"] = "vehicle-control"
    transport_id: str
    skill: int = Field(ge=1, le=50)
    modifier: int = Field(default=0, ge=-30, le=10)
    turning: bool = False
    climbing: bool = False
    # An authored outcome choice for space/submarine stress failure (B469),
    # never a random invented leak/engine-failure mechanic.


class VehicleImpact(Command):
    kind: Literal["vehicle-impact"] = "vehicle-impact"
    transport_id: str
    angle: Literal["immovable", "head-on", "rear-end", "side-on"]
    target_transport_id: str | None = None
    obstacle_item_id: str | None = None
    surface: Literal["hard", "soft"] = "hard"
    # Speed lost is adjudicated by the authoritative collision caller. B432
    # specifies lost velocity, not a universal rule that both vehicles stop.
    speed_after: int = Field(default=0, ge=0, le=100)
    target_speed_after: int = Field(default=0, ge=0, le=100)
    protection: tuple[PassengerProtection, ...] = ()


class VehicleRollover(Command):
    kind: Literal["vehicle-rollover"] = "vehicle-rollover"
    transport_id: str
    protection: tuple[PassengerProtection, ...] = ()


class VehicleSkid(Command):
    kind: Literal["vehicle-skid"] = "vehicle-skid"
    transport_id: str
    protection: tuple[PassengerProtection, ...] = ()


class UpgradeVehicle(Command):
    kind: Literal["vehicle-upgrade-v2"] = "vehicle-upgrade-v2"
    transport_id: str
