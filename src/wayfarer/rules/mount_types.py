"""Crew-served and vehicle-mounted ranged weapon facts (#357).

Artillery (B178) and Gunner (B198) govern a weapon that is mounted or served by
a crew rather than held. The mount bears the weapon's weight, so the firer's own
ST and grip are not what validate the shot; the crew is, and for a weapon laid
indirectly the target never sees the shot coming.

The numbers are pinned catalog metadata. Nothing here is inferred from a skill
name, a damage type or a weapon's weight.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MountSpec(BaseModel):
    """Explicit mount facts for one crew-served or vehicle-mounted weapon mode."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    # Everyone the weapon needs to fire, the gunner included.
    crew: int = Field(ge=1, le=20)
    # Seconds of laying the mount needs before an indirect shot. A weapon laid
    # directly over open sights needs none.
    laying_seconds: int = Field(ge=0, le=60)
    # An indirectly laid shot arrives without warning: the target gets no active
    # defense against it. A direct-fire mount is defended normally.
    indirect: bool = False
    # A vehicle mount travels with its vehicle; a ground mount is emplaced.
    vehicle_mounted: bool = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.indirect and not self.laying_seconds:
            raise ValueError("Indirect fire requires laying time")
        if self.laying_seconds and not self.indirect:
            raise ValueError("Laying time belongs to indirect fire")
        return self
