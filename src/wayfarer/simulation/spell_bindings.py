"""Authored spell targeting permissions; no player-supplied skill or resistance."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.models import Id, Record
from wayfarer.simulation.spells import SpellId


class SpellChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    spell_id: SpellId
    distance_yards: int = Field(default=0, ge=0, le=10000)
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    light_radius: int = Field(default=2, ge=0, le=100, exclude_if=lambda value: value == 2)
    light_penalty: int = Field(default=-3, ge=-9, le=0, exclude_if=lambda value: value == -3)


class BackfireAlternative(Record):
    """Campaign-authored interpretation of a contextual B236 result."""

    id: Id
    spell_id: SpellId
    rows: tuple[int, ...] = Field(min_length=1, max_length=18)
    severity: Literal["normal", "disaster"] = "normal"
    effect: Literal["retarget", "reverse", "damage", "summon", "waive", "reroll"]
    target_ids: tuple[Id, ...] = Field(min_length=1, max_length=100)
    relationship: Literal["caster", "companion", "foe", "any"] = "any"
    damage_dice: int = Field(default=1, ge=0, le=20)
    damage_add: int = Field(default=0, ge=-20, le=100)
    damage_type: Literal["burn", "cr", "tox"] = "burn"
    position: tuple[int, int] | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def bounds(self) -> BackfireAlternative:
        if any(row not in range(19) for row in self.rows) or len(set(self.target_ids)) != len(
            self.target_ids
        ):
            raise ValueError("Invalid backfire table rows or duplicate targets")
        return self


class SpellRules(Record):
    execution_version: Literal[1, 2] = Field(default=1, exclude_if=lambda value: value == 1)
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    channels: tuple[SpellChannel, ...]
    backfire_alternatives: tuple[BackfireAlternative, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def unique(self) -> SpellRules:
        if len({c.id for c in self.backfire_alternatives}) != len(self.backfire_alternatives):
            raise ValueError("Duplicate spell backfire alternative")
        if len({c.id for c in self.channels}) != len(self.channels):
            raise ValueError("Duplicate spell channel")
        return self
