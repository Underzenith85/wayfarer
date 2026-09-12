"""Prototype attack and protection profiles, injury traces and the combat rules record."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.combat.battlefield import Battlefield, BattlefieldTemplate
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.models import Id, Record


class AttackProfile(Record):
    """Server-authored original subset, bound to an implemented equipment entry."""

    definition_id: Id
    mode: Literal["melee"] = "melee"
    target: Literal["torso"] = "torso"
    attack_target: Literal["attribute:dx"] = "attribute:dx"
    attack_modifier: int = Field(default=0, ge=-20, le=20)
    damage_dice: int = Field(default=1, ge=1, le=10)
    damage_bonus: int = Field(default=0, ge=-10, le=100)
    injury_multiplier: int = Field(default=1, ge=1, le=4)
    stun_ticks: int = Field(default=1, ge=0, le=100)


class ProtectionProfile(Record):
    definition_id: Id
    resistance: int = Field(ge=0, le=100)


class InjuryTrace(Record):
    attack: CheckTrace
    defense: CheckTrace | None = None
    second_defense: CheckTrace | None = None
    attack_value: DerivedValue
    defense_value: DerivedValue | None = None
    damage_dice: tuple[int, ...] = ()
    basic_damage: int = Field(default=0, ge=0)
    resistance: int = Field(default=0, ge=0)
    injury: int = Field(default=0, ge=0)
    hp_before: int
    hp_after: int
    incapacitated: bool = False
    stunned_until: int | None = None
    profile_id: Id
    rules_version: str
    critical_table: tuple[int, ...] = ()
    malfunction_table: tuple[int, ...] = Field(default=(), exclude_if=lambda v: not v)
    malfunction: str | None = Field(default=None, exclude_if=lambda v: v is None)
    adjudication_required: str | None = None
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    lasting_injury_ids: tuple[str, ...] = ()
    shots_fired: int = Field(default=0, ge=0)
    hits: int = Field(default=0, ge=0)
    per_hit_damage: tuple[int, ...] = ()
    per_hit_injury: tuple[int, ...] = ()
    per_hit_resistance: tuple[int, ...] = Field(default=(), exclude_if=lambda v: not v)
    per_hit_locations: tuple[HumanLocation | None, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    per_hit_location_dice: tuple[tuple[int, ...], ...] = Field(
        default=(), exclude_if=lambda v: not v
    )


class CombatConsequence(Record):
    """Authored revelation after recorded incapacitation in a completed encounter."""

    id: Id
    battlefield_id: Id
    defeated_actor_id: Id
    recipient_actor_ids: tuple[Id, ...] = Field(min_length=1)
    fact_ids: tuple[Id, ...] = Field(min_length=1)


class CombatRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_allowance: int = Field(default=5, ge=1, le=100)
    prone_movement_allowance: int = Field(default=1, ge=0, le=100)
    default_reach: int = Field(default=1, ge=1, le=20)
    max_combatants: int = Field(default=30, ge=2, le=100)
    battlefields: tuple[BattlefieldTemplate, ...] = Field(default=(), max_length=100)

    consequences: tuple[CombatConsequence, ...] = Field(default=(), exclude=True)
    attacks: tuple[AttackProfile, ...] = Field(default=(), exclude=True)
    protection: tuple[ProtectionProfile, ...] = Field(default=(), exclude=True)
    gurps_equipment: EquipmentCatalog | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_unique(self) -> CombatRules:
        if any(b.location_id == "unbound" for b in self.battlefields):
            raise ValueError("Battlefield template requires an authored location")
        if len({b.id for b in self.battlefields}) != len(self.battlefields):
            raise ValueError("Duplicate battlefield ID")
        templates = {b.id: b for b in self.battlefields}
        for board in self.battlefields:
            if isinstance(board, HexBattlefield) and board.source_template_id is not None:
                source = templates.get(board.source_template_id)
                if not isinstance(source, Battlefield) or source.location_id != board.location_id:
                    raise ValueError(
                        "Migrated template requires its original square template at the same location"
                    )
        if len({p.definition_id for p in self.attacks}) != len(self.attacks) or len(
            {p.definition_id for p in self.protection}
        ) != len(self.protection):
            raise ValueError("Duplicate combat profile")
        return self
