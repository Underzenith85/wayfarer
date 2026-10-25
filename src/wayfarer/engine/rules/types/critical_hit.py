"""B556 body critical effects shared by delivery and canonical injury authentication."""

from typing import Literal

from pydantic import model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Record


class InnateHitEffects(Record):
    basic_multiplier: Literal[1, 2, 3] = 1
    maximum_damage: bool = False
    halve_dr: Literal["down"] | None = None
    force_major_wound: bool = False
    double_shock: bool = False
    drop_all_held: bool = False


def body_hit_effects(table: tuple[int, ...]) -> InnateHitEffects:
    """B556 ordinary body table; the host supports torso, not head/limb targeting."""
    if len(table) != 3 or any(type(d) is not int or not 1 <= d <= 6 for d in table):
        raise ValidationError("Critical table requires three captured six-sided dice")
    row = sum(table)
    return InnateHitEffects(
        basic_multiplier=3 if row in (3, 18) else 2 if row in (5, 16) else 1,
        maximum_damage=row in (6, 15),
        halve_dr="down" if row in (4, 17) else None,
        force_major_wound=row in (7, 13, 14),
        double_shock=row == 8,
        drop_all_held=row == 12,
    )


class CorrosionCritical(Record):
    """Private captured native torso table, distinct from physical armor identity."""

    table: tuple[int, int, int]

    @model_validator(mode="after")
    def exact(self) -> CorrosionCritical:
        body_hit_effects(self.table)
        return self

    @property
    def effects(self) -> InnateHitEffects:
        return body_hit_effects(self.table)
