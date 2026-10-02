"""Private canonical attack targets and immutable selected 3d6 traces."""

from dataclasses import replace

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import (
    CheckTrace,
    Modifier,
    Outcome,
    RandomSource,
    RecordedDice,
    evaluate_success,
)
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.models import Record


def score_attack(original: CheckTrace, dice: tuple[int, int, int], *, ranged: bool) -> CheckTrace:
    selected = evaluate_success(
        original.base_target,
        original.modifiers,
        dice,
        rules_package=original.rules_package,
        rules_version=original.rules_version,
        rule_id=original.rule_id,
    )
    if ranged and selected.outcome is Outcome.CRITICAL_FAILURE and selected.total < 17:
        selected = replace(selected, outcome=Outcome.FAILURE)
    return selected


class AttackRollSpec(Record):
    profile_id: str
    target: int
    modifiers: tuple[Modifier, ...] = ()
    ranged: bool = False
    rule_id: str = "gurps.check.success"

    def score(self, dice: tuple[int, int, int]) -> CheckTrace:
        return self.roll(RecordedDice(dice))

    def roll(self, rng: RandomSource) -> CheckTrace:
        original = success_roll(self.profile_id, self.target, self.modifiers, rng=rng)
        original = replace(original, rule_id=self.rule_id)
        return score_attack(original, original.dice, ranged=self.ranged)


class AttackRollChoice(Record):
    attacker_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    captured_attacker: ValidatedBuild | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    original: CheckTrace
    selected: CheckTrace
    ranged: bool = False

    @model_validator(mode="after")
    def captured_target(self) -> AttackRollChoice:
        if (self.attacker_id is None) != (self.captured_attacker is None):
            raise ValueError("Captured attack build requires its exact attacker identity")
        if score_attack(self.original, self.selected.dice, ranged=self.ranged) != self.selected:
            raise ValueError("Selected attack differs from its captured original context")
        return self
