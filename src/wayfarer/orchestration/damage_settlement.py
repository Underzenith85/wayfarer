"""Once-only combat movement and settlement after a selected damage phase."""

from dataclasses import replace

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.sensory_state import invalidate_movement
from wayfarer.engine.simulation.combat.tactical_transitions import finish_defense_with_movement
from wayfarer.engine.simulation.magic.staff_casting_state import (
    invalidate_movement as invalidate_staff_movement,
)
from wayfarer.engine.simulation.traits.composed_host import ResistComposedAttack
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.opponent_attack_privacy import hide_secret_totals
from wayfarer.orchestration.play import PlayService


def settle_damage_response(
    play: PlayService,
    before: PlayState,
    step: CombatStep,
    response: ChooseDefense | ResistComposedAttack,
    *,
    secret: bool = False,
) -> tuple[PlayState, CombatResult]:
    response = response.model_copy(update={"expected_revision": before.revision})
    context = CombatContext(play, before)
    if isinstance(response, ChooseDefense):
        encounter, moved = finish_defense_with_movement(
            play.rules_context, step.state, step.encounter, response
        )
        resources = invalidate_movement(
            step.resources, encounter.id, moved, revision=before.revision + 1
        )
        resources = invalidate_staff_movement(
            resources, encounter.id, moved, revision=before.revision + 1
        )
        step = replace(
            step,
            encounter=encounter,
            resources=resources,
            state=step.state.model_copy(update={"resources": resources}),
        )
    encounters = tuple(
        step.encounter if e.id == step.encounter.id else e for e in step.state.encounters
    )
    step, encounters = _settle_combat(step, response, encounters, context)
    state, combat = _finish_combat(step, response, encounters, context)
    if secret:
        state = hide_secret_totals(state, step.encounter.id, response.id)
    state = play.checkpoint(state, before=before)
    return state, combat
