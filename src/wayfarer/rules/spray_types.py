"""Liquid projector stream facts and the stream a firer holds open (#359).

A liquid projector (B205) does not fire a projectile that lands and is done. It
opens a stream: the firer holds it on a target, pays for every second it runs,
and rolls again each second. The numbers are pinned catalog metadata; nothing
here is inferred from a skill name or a damage type.
"""

from typing import Self

from pydantic import Field, model_validator

from wayfarer.models import Record


class SprayerSpec(Record):
    """Explicit stream facts for one liquid projector mode."""

    # The longest a single stream can run before it must stop.
    sustained_seconds: int = Field(ge=1, le=60)
    # Rounds the stream consumes for every second it is held, the first included.
    rounds_per_second: int = Field(ge=1, le=20)
    # Whether the stream can set its target alight under B433. The encounter
    # resolver binds a qualifying hit to an authoritative scene hazard.
    ignites: bool = False


class Stream(Record):
    """One stream a firer is currently holding, carried in the encounter."""

    weapon_id: str = Field(min_length=1)
    mode_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    seconds: int = Field(ge=1, le=60)
    sustained_seconds: int = Field(ge=1, le=60)
    ignites: bool = False

    @model_validator(mode="after")
    def within_its_ceiling(self) -> Self:
        if self.seconds > self.sustained_seconds:
            raise ValueError("A stream cannot run past its sustained-seconds ceiling")
        return self

    @property
    def exhausted(self) -> bool:
        return self.seconds >= self.sustained_seconds

    def sustain(self, target_id: str) -> Stream:
        """Hold the stream for another second, walking it to a new target if asked."""
        return self.model_copy(update={"seconds": self.seconds + 1, "target_id": target_id})
