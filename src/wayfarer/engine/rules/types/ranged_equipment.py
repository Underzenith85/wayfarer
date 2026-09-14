"""Typed B275-B281 ranged-equipment adapters.

These records describe only facts selected by the catalog.  Combat remains in
the existing ranged, injury, explosion, toxin, mount, and inventory services.
"""

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Id, Record


class AmmunitionVariant(Record):
    """One explicit alternative to a launcher's ordinary ammunition."""

    base_definition_id: Id
    damage_type: (
        Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn", "cor", "tox", "fat"] | None
    ) = None
    armor_divisor: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    damage_add: int = Field(default=0, ge=-20, le=20)
    damage_add_per_die: int = Field(default=0, ge=-10, le=10)
    range_multiplier: Decimal = Field(default=Decimal(1), gt=0, le=10, allow_inf_nan=False)
    follow_up_payload_id: Id | None = None

    @model_validator(mode="after")
    def changes_something(self) -> Self:
        if (
            self.damage_type is None
            and self.armor_divisor is None
            and not self.damage_add
            and not self.damage_add_per_die
            and self.range_multiplier == 1
            and self.follow_up_payload_id is None
        ):
            raise ValueError("An ammunition variant must change an explicit weapon fact")
        return self


class FollowUpSpec(Record):
    """A separately resisted affliction or toxin carried by a hit."""

    payload_id: Id
    kind: Literal["poison", "drug", "affliction"]
    resistance_penalty: int = Field(default=0, ge=-20, le=0)
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    condition: Literal["stun", "unconsciousness"] | None = None
    requires_penetration: bool = True
    duration_minutes_per_margin: int = Field(default=0, ge=0, le=60)


class BackBlastSpec(Record):
    """Burning hazard created behind a launcher when it fires."""

    dice: int = Field(ge=1, le=20)
    adds: int = Field(default=0, ge=-20, le=20)
    range_yards: int = Field(ge=1, le=100)


class BackBlastEvent(Record):
    """Persisted hazardous discharge awaiting positional damage resolution."""

    kind: Literal["back-blast-v1"] = "back-blast-v1"
    weapon_item_id: Id
    dice: int
    adds: int = 0
    range_yards: int


class WeaponAttachmentSpec(Record):
    """Launcher whose handling is supplied by an authored host weapon."""

    kind: Literal["under-barrel", "integral"]
    minimum_host_tl: int = Field(ge=0, le=12)
    host_definition_id: Id | None = None
    host_skill_ids: tuple[Id, ...] = ()
    inherit_bulk: bool = True

    @model_validator(mode="after")
    def integral_host_is_named(self) -> Self:
        if self.kind == "integral" and self.host_definition_id is None:
            raise ValueError("Integral launchers require their exact host definition")
        if self.kind == "under-barrel" and not self.host_skill_ids:
            raise ValueError("Under-barrel launchers require explicit host weapon skills")
        return self


class FollowUpResult(Record):
    """Replayable result of one selected follow-up payload."""

    payload_id: Id
    applied: bool
    reason: Literal["penetrated", "carrier-stopped", "resisted", "failed-resistance"]
    resistance_target: int | None = None
    resistance_total: int | None = None
    margin: int = 0
    duration_seconds: int = 0
