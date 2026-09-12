"""Pinned B407/B414-415 payloads and explicit GM blast responses."""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.models import Record


class ExplosionSpec(Record):
    dice: int = Field(ge=1, le=100)
    adds: int = Field(default=0, ge=-100, le=100)
    multiplier: int = Field(default=1, ge=1, le=100)
    damage_type: Literal["cr", "burn"] = "cr"
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    fragmentation_dice: int = Field(default=0, ge=0, le=100)


class BlastResponse(Record):
    """GM declares chosen defenses and scene cover before any blast dice."""

    actor_id: str
    # Required even when zero: cover is not inferred from absent map geometry.
    cover_dr: int = Field(ge=0)
    covered_locations: tuple[HumanLocation, ...] = ()
    dive_covered_locations: tuple[HumanLocation, ...] = ()
    size_modifier: int
    dive_to: GroundPosition | None = None
    dive_cover_dr: int = Field(default=0, ge=0)
