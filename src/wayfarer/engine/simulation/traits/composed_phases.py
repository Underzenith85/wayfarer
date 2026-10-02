"""Immutable inputs between composed attack, defense and damage phases.

These private records contain source facts and selected checks, never a prior
world or resource snapshot to restore when the next phase resumes.
"""

from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.simulation.combat.attack_roll import AttackRollChoice
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.traits.attack_defense import (
    AttackChannel,
    TraitAttackCommand,
    TraitAttackConsequences,
)
from wayfarer.engine.simulation.traits.composed_records import (
    ComposedPending,
    CurrentAttack,
    ResistComposedAttack,
)
from wayfarer.engine.simulation.traits.innate_criticals import InnateHitEffects
from wayfarer.engine.simulation.traits.malediction_checks import MaledictionPreparation
from wayfarer.models import Record


class ComposedAttackChoice(AttackRollChoice):
    """An original's immutable source and the private host's selected dice."""

    current: CurrentAttack
    ranged: bool = True
    malediction: MaledictionPreparation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ComposedDelivery(Record):
    command: ChooseDefense | ResistComposedAttack
    binding: ComposedPending
    current: CurrentAttack
    hp_before: int
    attack_captured: bool = Field(default=False, exclude_if=lambda value: not value)
    hit: bool
    defended: bool
    checks: tuple[CheckTrace, ...]
    selected: Literal["none", "dodge"]
    defense: CheckTrace | None
    value: DerivedValue | None
    effects: InnateHitEffects
    critical_table: tuple[int, ...]
    critical_id: str | None


class OwnerDamageArguments(Record):
    command: TraitAttackCommand
    attacker_build: ValidatedBuild
    target_build: ValidatedBuild
    channels: tuple[AttackChannel, ...]
    target_ht: int
    consequences: TraitAttackConsequences
