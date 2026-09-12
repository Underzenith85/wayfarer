"""Internal resolved vehicle commands. These are not part of frozen scenario/play v1."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.vehicle import PassengerProtection, WaterOccupantCheck
from wayfarer.engine.simulation.hex_geometry import HexFacing
from wayfarer.engine.simulation.resources import Command


class VehicleManeuver(Command):
    kind: Literal["vehicle-maneuver"] = "vehicle-maneuver"
    transport_id: str
    course: tuple[HexFacing, ...] = Field(max_length=100)
    end_speed: int = Field(ge=0, le=100)
    end_altitude: int | None = Field(default=None, ge=-1000, le=1000000)
    waterline: int | None = None
    control_skill: int | None = Field(default=None, ge=1, le=50)


class VehicleControl(Command):
    kind: Literal["vehicle-control"] = "vehicle-control"
    transport_id: str
    skill: int = Field(ge=1, le=50)
    modifier: int = Field(default=0, ge=-30, le=10)
    turning: bool = False
    climbing: bool = False
    water_occupants: tuple[WaterOccupantCheck, ...] = ()
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
    speed_after: int = Field(default=0, ge=0, le=1000000000)
    target_speed_after: int = Field(default=0, ge=0, le=1000000000)
    protection: tuple[PassengerProtection, ...] = ()


class VehicleRollover(Command):
    kind: Literal["vehicle-rollover"] = "vehicle-rollover"
    transport_id: str
    protection: tuple[PassengerProtection, ...] = ()


class VehicleSkid(Command):
    kind: Literal["vehicle-skid"] = "vehicle-skid"
    transport_id: str
    protection: tuple[PassengerProtection, ...] = ()
    target_transport_id: str | None = None
    target_actor_id: str | None = None
    target_q: int | None = Field(default=None, ge=-1000, le=1000)
    target_r: int | None = Field(default=None, ge=-1000, le=1000)
    impact_angle: Literal["head-on", "rear-end", "side-on"] | None = None
    speed_after: int = Field(default=0, ge=0, le=100)
    target_speed_after: int = Field(default=0, ge=0, le=100)


class ResolveVehicleEjection(Command):
    kind: Literal["vehicle-resolve-ejection"] = "vehicle-resolve-ejection"
    transport_id: str
    passenger_id: str
    destination_q: int = Field(ge=-1000, le=1000)
    destination_r: int = Field(ge=-1000, le=1000)
    landing: Literal["hard", "soft", "water"] = "hard"
    swimming_skill: int | None = Field(default=None, ge=1, le=50)


class ResolveAirAftermath(Command):
    kind: Literal["vehicle-resolve-air-aftermath"] = "vehicle-resolve-air-aftermath"
    transport_id: str
    protection: tuple[PassengerProtection, ...] = ()


class ResolveWaterAftermath(Command):
    kind: Literal["vehicle-resolve-water-aftermath"] = "vehicle-resolve-water-aftermath"
    transport_id: str
    action: Literal["drift", "right", "sink", "stress-leak"]
    skill: int | None = Field(default=None, ge=1, le=50)
    direction: HexFacing | None = None
    distance: int = Field(default=0, ge=0, le=100)
    waterline: int | None = None
    leak_damage: int = Field(default=0, ge=0, le=100)
    occupants: tuple[WaterOccupantCheck, ...] = ()


class NavigateSpace(Command):
    kind: Literal["vehicle-space-navigation"] = "vehicle-space-navigation"
    transport_id: str
    action: Literal["burn", "coast"]
    target_speed: int = Field(ge=0, le=1000000000)
    course: tuple[HexFacing, ...] = Field(default=(), max_length=100)
    miles_per_hex: int = Field(default=0, ge=0, le=1000000000)


class ResolveMountSeparation(Command):
    kind: Literal["vehicle-resolve-mount-separation"] = "vehicle-resolve-mount-separation"
    transport_id: str
    riding_skill: int | None = Field(default=None, ge=1, le=50)
    collision_speed: int = Field(default=0, ge=0, le=1000000000)


class VehicleRam(Command):
    kind: Literal["vehicle-ram"] = "vehicle-ram"
    transport_id: str
    target_transport_id: str
    skill: int = Field(ge=1, le=50)
    defense: Literal["dodge", "none"] = "dodge"
    defender_skill: int | None = Field(default=None, ge=1, le=50)
    angle: Literal["head-on", "rear-end", "side-on"] = "head-on"
    speed_after: int = Field(default=0, ge=0, le=1000000000)
    target_speed_after: int = Field(default=0, ge=0, le=1000000000)
    protection: tuple[PassengerProtection, ...] = ()


class DamageVehicle(Command):
    kind: Literal["vehicle-damage"] = "vehicle-damage"
    transport_id: str
    basic_damage: int = Field(ge=0, le=1000000)
    damage_type: Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn"]
    hit_location: Literal["hull", "motive", "controls", "weapon"] = "hull"
    equipment_item_id: str | None = None
    operator_damage: int = Field(default=0, ge=0, le=1000000)
    operator_ht: int | None = Field(default=None, ge=1, le=50)


class UpgradeVehicle(Command):
    kind: Literal["vehicle-upgrade-v2"] = "vehicle-upgrade-v2"
    transport_id: str
