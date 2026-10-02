"""Captured mechanical inputs at the inventory melee damage boundary."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, PendingDefense
from wayfarer.engine.simulation.equipment.catalog import DamageType, MeleeMode
from wayfarer.engine.simulation.resources import Pool, SilverConstruction
from wayfarer.models import Record


class MeleeDamageInputs(Record):
    """Fixed source, prior checks and local mechanical facts, never a state to restore."""

    pending: PendingDefense
    weapon: MeleeMode
    construction: SilverConstruction | None = None
    attack_build: ValidatedBuild
    defend_build: ValidatedBuild
    attacker: Combatant
    defender: Combatant
    hp: Pool
    attack: CheckTrace
    defense: CheckTrace | None
    second_trace: CheckTrace | None
    attack_value: DerivedValue
    defense_derived: DerivedValue | None
    defense_item: str | None
    critical: int | None
    critical_dice: tuple[int, ...]
    critical_tables: tuple[tuple[int, ...], ...]
    critical_parry_mode: str | None
    critical_eye: bool
    blocked: str | None
    cattle_prod: bool
    damage_type: DamageType
    head: bool
    hit: bool
    shield_hit: str | None
    maximum: bool
    dice_count: int = Field(ge=1)
    adds: int
    effect_dice: tuple[int, ...]
    lasting_ids: tuple[str, ...]
    location: HumanLocation | None
    location_dice: tuple[int, ...]
    vulnerability_multiplier: Decimal

    @property
    def rollable(self) -> bool:
        return bool((self.hit or self.shield_hit) and not self.maximum)


class PreparedMeleeDamage(Record):
    inputs: MeleeDamageInputs
    original: tuple[int, ...] | None
    secret: bool = False

    @property
    def dice_count(self) -> int:
        return self.inputs.dice_count

    @property
    def rollable(self) -> bool:
        return self.inputs.rollable

    @model_validator(mode="after")
    def valid_original(self) -> PreparedMeleeDamage:
        if self.rollable:
            if self.original is None:
                if not self.secret:
                    raise ValueError("An ordinary damage opportunity requires its original")
            elif (
                self.secret
                or len(self.original) != self.dice_count
                or any(type(die) is not int or not 1 <= die <= 6 for die in self.original)
            ):
                raise ValueError("Damage original disagrees with its captured expression")
        elif self.original != ():
            raise ValueError("This melee delivery has no random damage")
        return self


@dataclass(frozen=True)
class MeleeDamageStage:
    state: PlayState
    encounter: Encounter
    preparation: PreparedMeleeDamage
