"""Project approved movement/form purchases into trusted runtime capabilities."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.traits.movement_forms import BINDING_BY_ID, PROFILE, metadata
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class PurchasedMovementForm(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = ()


class MovementForms(Record):
    entries: tuple[PurchasedMovementForm, ...] = ()

    def purchase(self, identifier: str) -> PurchasedMovementForm | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def level(self, identifier: str) -> int:
        selected = self.purchase(identifier)
        return 0 if selected is None else selected.levels

    def has(self, identifier: str) -> bool:
        return self.purchase(identifier) is not None

    def parameter(self, identifier: str, name: str) -> str | int | bool | None:
        selected = self.purchase(identifier)
        return None if selected is None else dict(selected.parameters).get(name)

    def actions_per_turn(self) -> int:
        return max(
            1,
            1
            + self.level("advantage:altered-time-rate")
            - self.level("disadvantage:decreased-time-rate"),
        )

    def top_move(self, basic_move: int, mode: str = "ground") -> int:
        if (
            type(basic_move) is not int
            or basic_move < 0
            or mode not in {"ground", "air", "water", "space"}
        ):
            raise ValidationError("Invalid movement calculation")
        enhanced = self.purchase("advantage:enhanced-move")
        levels = (
            enhanced.levels if enhanced and dict(enhanced.parameters).get("mode") == mode else 0
        )
        move = basic_move * (1 << levels)
        if mode == "air" and self.has("advantage:flight"):
            move = max(move, basic_move * 2)
        if mode == "water" and not self.has("advantage:amphibious"):
            move //= 5
        if self.parameter("disadvantage:no-legs", "form") == "sessile":
            return 0
        return move

    def effective_lifting_st(self, strength: int) -> int:
        if type(strength) is not int or strength < 0:
            raise ValidationError("Invalid lifting ST")
        return strength + self.level("advantage:lifting-st") + self.level("advantage:telekinesis")

    def jump_multiplier(self) -> int:
        return 1 << self.level("advantage:super-jump")

    def climbing_bonus(self) -> int:
        return 2 * self.level("advantage:super-climbing")

    def fall_distance(self, yards: int, *, conscious: bool = True) -> int:
        if type(yards) is not int or yards < 0:
            raise ValidationError("Invalid fall distance")
        if conscious and self.has("advantage:catfall"):
            return max(0, yards - 5)
        return yards

    def manipulator_penalty(self, fine_work: bool = True) -> int:
        if self.has("disadvantage:no-manipulators"):
            return -10
        if fine_work and self.has("disadvantage:no-fine-manipulators"):
            return -6
        return 0

    def dodge_modifier(self) -> int:
        legs = self.parameter("advantage:extra-legs", "legs")
        return 0 if legs is None else min(3, max(0, int(legs) - 2))

    def size_modifier_delta(self) -> int:
        return self.level("advantage:growth") - self.level("advantage:shrinking")


NO_MOVEMENT_FORMS = MovementForms()


def movement_forms(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> MovementForms:
    entries: list[PurchasedMovementForm] = []
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_MOVEMENT_FORMS
    for purchase in build.trait_purchases:
        binding = BINDING_BY_ID.get(purchase.definition_id)
        definition = definitions.get(purchase.definition_id)
        if binding is None or definition is None or purchase.trait is None:
            # Unparameterized purchases normally omit TraitOptions; preserve them.
            options_parameters: tuple[tuple[str, str | int | bool], ...] = ()
            options_modifiers: tuple[str, ...] = ()
        else:
            options_parameters = purchase.trait.parameters
            options_modifiers = purchase.trait.modifiers
        if (
            binding is None
            or binding.manual
            or definition is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules != metadata(binding)
            or binding.hook not in definition.trait_rules.runtime_hooks
        ):
            continue
        entries.append(
            PurchasedMovementForm(
                definition_id=purchase.definition_id,
                levels=purchase.amount,
                parameters=options_parameters,
                modifiers=options_modifiers,
            )
        )
    return MovementForms(entries=tuple(entries))
