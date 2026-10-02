"""Private actor-relative maneuver opportunities within one real combat turn."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.character.traits.movement_forms import movement_forms
from wayfarer.engine.simulation.combat.encounter import Encounter, ManeuverBudget
from wayfarer.engine.simulation.combat.generations import maneuver_budget_enabled
from wayfarer.engine.simulation.magic.great_haste_effects import bonus

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.rules_context import RulesContext


def begin(runtime: RulesContext, state: PlayState, encounter: Encounter) -> Encounter:
    """Freeze an accepted actor's opportunities before its first real maneuver."""
    if (
        not maneuver_budget_enabled()
        or encounter.maneuver_budget is not None
        or encounter.wait_interrupt is not None
    ):
        return encounter
    actor = encounter.current_actor_id
    build = runtime.approved_build(state, actor)
    native_total = movement_forms(build, runtime.reviewer.compiler.definitions).actions_per_turn()
    spell_bonus = bonus(state.resources, actor)
    count = native_total + spell_bonus
    if count == 1:
        return encounter
    return encounter.model_copy(
        update={
            "maneuver_budget": ManeuverBudget(
                actor_id=actor,
                round=encounter.round,
                turn_index=encounter.turn_index,
                native_total=native_total,
                spell_bonus=spell_bonus,
                accepted_build=build,
                total=count,
                remaining=count,
            )
        }
    )


def first_opportunity(encounter: Encounter) -> bool:
    budget = encounter.maneuver_budget
    return budget is None or budget.remaining == budget.total


def last_opportunity(encounter: Encounter) -> bool:
    budget = encounter.maneuver_budget
    return budget is None or budget.remaining == 1
