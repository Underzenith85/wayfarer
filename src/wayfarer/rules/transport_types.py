"""Explicit ground transport checkpoint schema; Campaigns fourth printing B396-397, B469."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Record
from wayfarer.rules.vehicle_types import Locomotion, PassengerEjection, VehicleTrace


class Transport(Record):
    source_id: Literal["gurps-basic-set-campaigns-4e-fourth-printing"] = (
        "gurps-basic-set-campaigns-4e-fourth-printing"
    )
    control_dice: tuple[int, int, int] | None = None
    control_target: int | None = None
    control_margin: int | None = None
    id: str = Field(min_length=1, max_length=200)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    mechanics_version: Literal[1, 2] = 1
    altitude: int = 0
    vertical_speed: int = Field(default=0, ge=0, le=100)
    aftermath_turn: int = Field(default=-1, ge=-1)
    minimum_speed: int = Field(default=0, ge=0)
    draft: int = Field(default=0, ge=0)
    draft_inches: int = Field(default=0, ge=0, le=11)
    submersion: int = Field(default=0, ge=0, le=1000000)
    sink_rate: int = Field(default=1, ge=1, le=1000)
    leak_rate: int = Field(default=0, ge=0, le=1000)
    open_cabin: bool = False
    unsinkable: bool = False
    straight_yards: int = Field(default=0, ge=0)
    recovery_turn: int = Field(default=-1, ge=-1)
    remaining_points: int = Field(default=0, ge=0, le=100)
    skid_thirds: int = Field(default=0, ge=0)
    subhex_thirds: int = Field(default=0, ge=0, le=2)
    traces: tuple[VehicleTrace, ...] = ()
    pending_ejections: tuple[PassengerEjection, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    overboard: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)
    locomotion: Locomotion
    body_id: str = Field(min_length=1, max_length=200)
    operator_id: str = Field(min_length=1, max_length=200)
    occupants: tuple[str, ...] = ()
    acceleration: int = Field(gt=0, le=100)
    top_speed: int = Field(gt=0, le=100)
    handling: int = Field(default=0, ge=-10, le=10)
    stability: int = Field(default=1, ge=1, le=10)
    speed: int = Field(default=0, ge=0, le=100)
    q: int = Field(default=0, ge=-1000, le=1000)
    r: int = Field(default=0, ge=-1000, le=1000)
    facing: Literal[0, 1, 2, 3, 4, 5] = 0
    # Authored longitudinal footprint, with the reference hex at offset zero.
    footprint: tuple[int, ...] = (0,)
    footprint_offsets: tuple[tuple[int, int], ...] = Field(default=(), max_length=100)
    status: Literal[
        "controlled",
        "skidding",
        "crashed",
        "spooked",
        "lost",
        "diving",
        "stalled",
        "capsized",
        "sinking",
        "drifting",
        "stress-failure",
        "ejection-pending",
        "control-required",
    ] = "controlled"
    last_turn: int = Field(default=-1, ge=-1)
    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)
    attack_penalty: int = Field(default=0, le=0)
    aim_lost: bool = False
    restraints: Literal["none", "seatbelts", "airbags"] = "none"

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len(set(self.occupants)) != len(self.occupants) or (
            self.operator_id not in self.occupants
            and not any(e.actor_id == self.operator_id for e in self.pending_ejections)
            and self.operator_id not in self.overboard
            and self.status != "crashed"
        ):
            raise ValueError("Transport requires a unique manifest including its operator")
        if len({e.actor_id for e in self.pending_ejections}) != len(self.pending_ejections) or set(
            self.occupants
        ) & {e.actor_id for e in self.pending_ejections}:
            raise ValueError("Pending ejections must be unique and outside the manifest")
        if len(set(self.overboard)) != len(self.overboard) or set(self.occupants) & set(
            self.overboard
        ):
            raise ValueError("Overboard occupants must be unique and outside the manifest")
        if self.mechanics_version == 1 and self.locomotion not in (
            "ground-wheeled",
            "ground-mount",
        ):
            raise ValueError("New locomotion modes require explicit transport version 2")
        if self.footprint_offsets and (
            self.mechanics_version != 2
            or (0, 0) not in self.footprint_offsets
            or len(set(self.footprint_offsets)) != len(self.footprint_offsets)
        ):
            raise ValueError(
                "Planar footprints require version 2 and unique offsets including origin"
            )
        if self.minimum_speed > self.top_speed:
            raise ValueError("Minimum speed exceeds top speed")
        if self.speed > self.top_speed:
            raise ValueError("Transport exceeds top speed")
        if (
            not self.footprint
            or 0 not in self.footprint
            or len(set(self.footprint)) != len(self.footprint)
        ):
            raise ValueError("Transport requires a unique footprint including its reference hex")
        if sorted(self.footprint) != list(range(min(self.footprint), max(self.footprint) + 1)):
            raise ValueError("Transport footprint must be contiguous")
        if self.locomotion == "ground-mount" and (
            self.occupants != (self.operator_id,) or self.restraints != "none"
        ):
            raise ValueError("Mount slice supports one unrestrained rider")
        return self
