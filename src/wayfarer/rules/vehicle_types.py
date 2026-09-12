"""Versioned vehicle facts; Basic Set Campaigns 4e fourth printing B394-395, B430-432, B468-470."""

from typing import Literal

from pydantic import Field

from wayfarer.models import Record

Locomotion = Literal[
    "ground-wheeled",
    "ground-tracked",
    "ground-drawn",
    "ground-walking",
    "ground-slithering",
    "ground-mount",
    "water",
    "underwater",
    "air",
    "space",
]


class PassengerProtection(Record):
    actor_id: str = Field(min_length=1)
    worn_dr: int = Field(default=0, ge=0)
    innate_dr: int = Field(default=0, ge=0)
    belted: bool = False
    airbag: bool = False
    strength: int | None = Field(default=None, ge=3)


class PassengerEjection(Record):
    """Unbelted occupant awaiting authoritative tactical placement (B432)."""

    actor_id: str = Field(min_length=1)
    origin_q: int = Field(ge=-1000, le=1000)
    origin_r: int = Field(ge=-1000, le=1000)
    facing: Literal[0, 1, 2, 3, 4, 5]
    distance_yards: int = Field(gt=0, le=1000)
    collision_speed: int = Field(gt=0, le=1000)


class VehicleTrace(Record):
    command_id: str
    reason: str
    actor_id: str
    dice: tuple[int, ...] = ()
    basic_damage: int = 0
    injury: int = 0
    ejection_yards: int = 0
    destination_q: int | None = Field(default=None, exclude_if=lambda value: value is None)
    destination_r: int | None = Field(default=None, exclude_if=lambda value: value is None)
    target: int | None = None
    margin: int | None = None
