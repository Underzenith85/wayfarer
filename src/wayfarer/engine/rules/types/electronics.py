"""Typed electronics capabilities from Campaigns B471-472.

The records describe authored hardware.  Stateful use and information release
belong to :mod:`wayfarer.engine.simulation.equipment.electronics`.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from wayfarer.models import Id, Record

Positive = Annotated[int, Field(ge=1)]
Nonnegative = Annotated[int, Field(ge=0)]


class CommunicatorSpec(Record):
    operator_skill_id: Id = "skill:electronics-operation-communications"
    media: tuple[Literal["code", "voice", "text", "video", "data"], ...] = Field(min_length=1)
    range_yards: Positive | None = None
    requires_address: bool = False
    secure: bool = False


class SensorSpec(Record):
    operator_skill_id: Id = "skill:electronics-operation-sensors"
    form: Literal["hands-free", "manual", "sight", "mounted"]
    sense: Literal[
        "vision",
        "night-vision",
        "infravision",
        "hyperspectral-vision",
        "metal-detection",
        "radar",
        "sonar",
        "ladar",
    ]
    range_yards: Positive | None = None
    active: bool = False
    new_sense: bool = False
    telescopic_vision: Nonnegative = 0
    night_vision: Nonnegative = 0
    picture: bool = True


class ComputerSpec(Record):
    operator_skill_id: Id = "skill:computer-operation"
    complexity: Nonnegative
    storage_megabytes: Positive
    has_terminal: bool = True

    def program_capacity(self, complexity: int) -> int:
        """B472: two programs at full Complexity, tenfold per level below."""
        if complexity < 0 or complexity > self.complexity:
            return 0
        return cast(int, 2 * 10 ** (self.complexity - complexity))


class ElectronicsSuite(Record):
    communicator: CommunicatorSpec | None = None
    sensor: SensorSpec | None = None
    computer: ComputerSpec | None = None
    power_capacity_seconds: Positive | None = None

    @model_validator(mode="after")
    def useful_suite(self) -> Self:
        if self.communicator is None and self.sensor is None and self.computer is None:
            raise ValueError("Electronics suite requires a device capability")
        return self


class ComputerProgram(Record):
    id: Id
    complexity: Nonnegative
    storage_megabytes: Positive
    task_skill_id: Id
    mode: Literal["bonus", "required"]
    bonus: int = 0
    native_technology_level: Nonnegative | None = None

    @model_validator(mode="after")
    def valid_program(self) -> Self:
        if self.mode == "required" and self.bonus:
            raise ValueError("Required task software is not a generic equipment bonus")
        return self
