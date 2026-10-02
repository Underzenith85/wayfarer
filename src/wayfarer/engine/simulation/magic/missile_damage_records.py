"""Fixed spell delivery facts before a held missile's own damage is chosen."""

from dataclasses import dataclass
from typing import Literal

from pydantic import model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, PendingDefense
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEffect
from wayfarer.models import Record


class MissileDamageInputs(Record):
    pending: PendingDefense
    effect: RuntimeSpellEffect
    attacker_id: str
    defender: Combatant
    target_ht: int
    hp_before: int
    attack: CheckTrace
    defended: CheckTrace | None
    second_roll: CheckTrace | None
    value: DerivedValue
    defense: DerivedValue | None
    critical: tuple[int, ...]
    row: int
    blocked: bool
    shield_hit: str | None
    maximum: bool
    hit: bool
    distance: int
    generation: Literal["legacy", "pending-damage"] = "pending-damage"
    resistance: int
    dropped: tuple[str, ...]

    @property
    def half_damage(self) -> bool:
        return self.distance >= 25 if self.generation == "pending-damage" else self.distance > 25

    @property
    def rollable(self) -> bool:
        return bool((self.hit or self.shield_hit) and not self.blocked and not self.maximum)


class PreparedMissileDamage(Record):
    inputs: MissileDamageInputs
    original: tuple[int, ...] | None
    secret: bool = False

    @property
    def dice_count(self) -> int:
        return self.inputs.effect.energy

    @property
    def rollable(self) -> bool:
        return self.inputs.rollable

    @model_validator(mode="after")
    def valid_original(self) -> PreparedMissileDamage:
        if not self.rollable or (self.original is None) != self.secret:
            raise ValueError("Pending missile damage requires its actual unresolved roll")
        if self.original is not None and (
            len(self.original) != self.dice_count
            or any(type(die) is not int or not 1 <= die <= 6 for die in self.original)
        ):
            raise ValueError("Missile damage original differs from its captured energy")
        return self


@dataclass(frozen=True)
class MissileDamageStage:
    state: PlayState
    encounter: Encounter
    preparation: PreparedMissileDamage
