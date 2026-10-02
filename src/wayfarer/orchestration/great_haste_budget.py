"""Finish exhausted spell-derived maneuver opportunities through canonical settlement."""

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import injury_turn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.magic.great_haste_effects import bonus
from wayfarer.engine.simulation.resources import Command
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def checkpoint(play: PlayService, state: PlayState) -> PlayState:
    engine = play.engine.combat
    if engine is None:
        return state
    for selected in tuple(state.encounters):
        encounter = next(e for e in state.encounters if e.id == selected.id)
        budget = encounter.maneuver_budget
        if encounter.status != "active" or budget is None or not budget.spell_bonus:
            continue
        if bonus(state.resources, budget.actor_id):
            continue
        remaining = budget.remaining - budget.spell_bonus
        pending = bool(
            encounter.pending_defense or encounter.pending_unarmed or encounter.wait_interrupt
        )
        if remaining > 0 or pending:
            # An already-launched maneuver finishes normally; no future one is minted.
            trimmed = encounter.model_copy(
                update={
                    "maneuver_budget": budget.model_copy(
                        update={
                            "remaining": max(1, remaining),
                            "spell_bonus": 0,
                        }
                    )
                }
            )
            state = state.model_copy(
                update={
                    "encounters": tuple(
                        trimmed if e.id == encounter.id else e for e in state.encounters
                    )
                }
            )
            continue
        before = state
        identifier = (
            "great-haste-budget:"
            + hashlib.sha256(
                f"{encounter.id}:{budget.actor_id}:{budget.round}:{budget.turn_index}".encode()
            ).hexdigest()
        )
        state = injury_turn(
            play.rules_context,
            state,
            budget.actor_id,
            identifier,
            start=False,
            do_nothing=next(
                p.last_maneuver for p in encounter.participants if p.actor_id == budget.actor_id
            )
            == "do_nothing",
            captured_end_build=budget.accepted_build
            if next(a.approval for a in state.actors if a.actor_id == budget.actor_id) is None
            else None,
        )
        resources = state.resources.model_copy(update={"revision": before.resources.revision})
        state = state.model_copy(update={"resources": resources})
        finished = engine._advance(encounter.model_copy(update={"maneuver_budget": None}))
        result = CombatResult(
            encounter_id=encounter.id,
            code="combat.great_haste_ended",
            round=finished.round,
            current_actor_id=finished.current_actor_id,
        )
        context = CombatContext(play, before)
        step = CombatStep(state, finished, resources, result)
        encounters = tuple(finished if e.id == finished.id else e for e in state.encounters)
        command = Command(
            id=identifier, actor_id=budget.actor_id, expected_revision=before.revision
        )
        step, encounters = _settle_combat(step, command, encounters, context)
        state, _ = _finish_combat(step, command, encounters, context, advance_revision=False)
    return state
