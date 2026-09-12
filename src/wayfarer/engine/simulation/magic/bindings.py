"""Authored spell targeting permissions; no player-supplied skill or resistance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.magic.protocols import (
    AreaSelection,
    CeremonialPlan,
    MagicItemBinding,
    MagicTradition,
    validate_tradition,
)
from wayfarer.engine.simulation.magic.spells import SpellId
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState


class SpellChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    spell_id: SpellId
    distance_yards: int = Field(default=0, ge=0, le=10000)
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    tradition: MagicTradition = Field(
        default="standard", exclude_if=lambda value: value == "standard"
    )
    ceremonial: CeremonialPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    magic_item_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    area: AreaSelection | None = Field(default=None, exclude_if=lambda value: value is None)
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
    enabled_optional_rules: frozenset[str] = Field(
        default=frozenset(), exclude_if=lambda value: not value
    )
    magic_items: tuple[MagicItemBinding, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    backfire_alternatives: tuple[BackfireAlternative, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def unique(self) -> SpellRules:
        if len({c.id for c in self.backfire_alternatives}) != len(self.backfire_alternatives):
            raise ValueError("Duplicate spell backfire alternative")
        if len({c.id for c in self.channels}) != len(self.channels):
            raise ValueError("Duplicate spell channel")
        if len({c.id for c in self.magic_items}) != len(self.magic_items):
            raise ValueError("Duplicate magic-item binding")
        for channel in self.channels:
            validate_tradition(
                channel.tradition, enabled_optional_rules=self.enabled_optional_rules
            )
            if channel.ceremonial and channel.ceremonial.leader_id != channel.actor_id:
                raise ValueError("Ceremonial channel leader must be its caster")
            if channel.magic_item_id is not None and not any(
                item.item_id == channel.magic_item_id and item.spell_id == channel.spell_id
                for item in self.magic_items
            ):
                raise ValueError("Magic-item channel requires a matching binding")
        return self


def validate_channels(rules: SpellRules, state: PlayState) -> None:
    """Spell channels and backfire alternatives must reference approved world entities."""
    entities = {e.id: e for e in state.world.entities}
    actor_ids = {a.actor_id for a in state.actors}
    if any(t not in actor_ids for option in rules.backfire_alternatives for t in option.target_ids):
        raise ValidationError("Backfire alternatives require approved campaign actors")
    for channel in rules.channels:
        if (
            channel.actor_id not in entities
            or channel.target_id not in entities
            or channel.location_id not in entities
            or entities[channel.location_id].kind is not EntityKind.LOCATION
        ):
            raise ValidationError("Invalid spell spell_channel entity references")
        if channel.ceremonial:
            participants = {c.actor_id for c in channel.ceremonial.contributions} | set(
                channel.ceremonial.opposing_spectators
            )
            if not participants <= actor_ids:
                raise ValidationError("Ceremonial magic requires approved campaign actors")
        if channel.magic_item_id is not None and channel.magic_item_id not in {
            item.id for item in state.resources.items
        }:
            raise ValidationError("Magic-item channel requires its configured item")
