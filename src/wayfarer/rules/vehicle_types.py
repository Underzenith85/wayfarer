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


class VehicleTrace(Record):
    command_id: str
    reason: str
    actor_id: str
    dice: tuple[int, ...] = ()
    basic_damage: int = 0
    injury: int = 0
    ejection_yards: int = 0
    target: int | None = None
    margin: int | None = None
