"""Launcher-assisted throws (#360): a spear thrower and the spear it launches.

Every other thrown row uses the projectile's own item: it is the weapon, it
leaves inventory, and it is retained in `expended_items`. A spear thrower
(B222) is a separate held launcher that improves a spear it does not consume.
The facts are pinned catalog metadata on the projectile's thrown mode, so a
launcher never changes what a weapon does by being named.
"""

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from wayfarer.models import Record


class LauncherSpec(Record):
    """The launcher one thrown mode requires, and what it does for the throw."""

    # The pinned catalog entry the thrower must be holding. A throw without it
    # is a different mode, not this one at a penalty.
    launcher_definition_id: str = Field(min_length=1)
    # What the launcher multiplies the throw's ranges by, and adds to its
    # damage. Both are pinned per weapon, never derived from the skill.
    # A launcher may extend the throw, add to its damage, or both, but a
    # launcher that does neither is not a launcher.
    range_multiplier: Decimal = Field(ge=1, le=10, allow_inf_nan=False)
    damage_bonus: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def improves_the_throw(self) -> Self:
        if self.range_multiplier == 1 and not self.damage_bonus:
            raise ValueError("A launcher must change the throw it assists")
        return self
