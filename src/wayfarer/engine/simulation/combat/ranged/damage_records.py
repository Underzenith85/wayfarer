"""Immutable delivery and per-projectile progress for owner damage choices."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.types.firearm import FirearmFailure
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.rules.types.ranged_equipment import FollowUpSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    Encounter,
    PendingDefense,
    RangedSituation,
)
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.engine.simulation.resources import AmmunitionLoad, Item, Pool, SilverConstruction
from wayfarer.models import Record


class RangedDamageContext(Record):
    pending: PendingDefense
    weapon: RangedMode
    construction: SilverConstruction | None = None
    actor: Combatant
    target: Combatant
    original_target: Combatant
    compiled: ValidatedBuild
    defender_build: ValidatedBuild
    hp: Pool
    hp_before: int
    attack: CheckTrace
    original_attack: CheckTrace
    defense: CheckTrace | None
    second_trace: CheckTrace | None
    value: DerivedValue
    defense_value: DerivedValue | None
    scene: RangedSituation
    critical: int | None
    critical_eye: bool
    critical_table: tuple[int, ...]
    critical_rolls: tuple[tuple[int, int, int], ...]
    critical_parry_mode: str | None
    parry_item: str | None
    head: bool
    blocked: str | None
    close_projectile_multiplier: int
    shield_hit: str | None
    shield_impacts: int
    hits: int
    impacts: int
    shots_fired: int
    effective_shots: int
    malfunction_table: tuple[int, ...]
    failure: FirearmFailure | None
    follow_up: FollowUpSpec | None
    vulnerability_multiplier: Decimal
    count: int = Field(ge=1)
    adds: int
    half: bool
    resistance_damage: Damage
    resistance_weapon: RangedMode
    dr_bonus: int
    environmental_dr: int
    vehicle_cover: int
    base_location: HumanLocation | None
    base_location_dice: tuple[int, ...]
    first_location: HumanLocation | None
    first_location_dice: tuple[int, ...]
    first_dr: int
    original_ammunition: AmmunitionLoad | None
    original_items: tuple[Item, ...]
    original_pools: tuple[Pool, ...]


class RangedDamageProgress(Record):
    index: int = Field(default=0, ge=0)
    location: HumanLocation | None
    location_dice: tuple[int, ...]
    dr: int
    damages: tuple[int, ...] = ()
    injuries: tuple[int, ...] = ()
    damage_dice: tuple[int, ...] = ()
    hit_resistances: tuple[int, ...] = ()
    hit_locations: tuple[HumanLocation | None, ...] = ()
    hit_location_dice: tuple[tuple[int, ...], ...] = ()
    effect_dice: tuple[int, ...]
    lasting_ids: tuple[str, ...]


class PreparedRangedDamage(Record):
    context: RangedDamageContext
    progress: RangedDamageProgress
    original: tuple[int, ...] | None
    secret: bool = False

    @property
    def dice_count(self) -> int:
        return self.context.count

    @model_validator(mode="after")
    def valid_original(self) -> PreparedRangedDamage:
        if self.progress.index >= self.context.impacts or self.context.blocked is not None:
            raise ValueError("A ranged damage opportunity requires its next projectile")
        if self.original is None:
            if not self.secret:
                raise ValueError("Ordinary ranged damage requires its original")
        elif (
            self.secret
            or len(self.original) != self.dice_count
            or any(type(die) is not int or not 1 <= die <= 6 for die in self.original)
        ):
            raise ValueError("Ranged damage original disagrees with its expression")
        return self


@dataclass(frozen=True)
class RangedDamageStage:
    state: PlayState
    encounter: Encounter
    preparation: PreparedRangedDamage
