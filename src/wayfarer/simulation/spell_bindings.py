"""Authored spell targeting permissions; no player-supplied skill or resistance."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.simulation.resources import Id, Record
from wayfarer.simulation.spells import SpellId


class SpellChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    spell_id: SpellId
    distance_yards: int = Field(default=0, ge=0, le=10000)
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"


class SpellRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    channels: tuple[SpellChannel, ...]

    @model_validator(mode="after")
    def unique(self) -> SpellRules:
        if len({c.id for c in self.channels}) != len(self.channels):
            raise ValueError("Duplicate spell channel")
        return self
