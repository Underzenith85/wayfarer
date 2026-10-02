"""Private Great Haste Concentrate maneuvers through canonical combat settlement."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import injury_turn
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.explosions import guard as explosion_guard
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.maneuver_budget import (
    begin,
    first_opportunity,
    last_opportunity,
)
from wayfarer.engine.simulation.combat.turn_commitment import prepare as prepare_commitment
from wayfarer.engine.simulation.magic.great_haste_casting import (
    PREFIX,
    CastingOrigin,
    origins,
    subjective_casting,
)
from wayfarer.engine.simulation.magic.great_haste_host import apply_host
from wayfarer.engine.simulation.magic.great_haste_state import (
    CastGreatHaste,
    GreatHasteReceipt,
    save,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.combat.encounters import _prepare_encounter
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.combat.turns import _validate_turn

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def cast_in_combat(
    play: PlayService, before: PlayState, command: CastGreatHaste
) -> tuple[PlayState, GreatHasteReceipt]:
    encounter = next(
        e for e in before.encounters if e.status == "active" and command.actor_id in e.turn_order
    )
    if command.operation == "complete":
        raise ValidationError("Combat Great Haste completes within its final Concentrate maneuver")
    if (
        encounter.current_actor_id != command.actor_id
        or encounter.pending_defense
        or encounter.pending_unarmed
        or encounter.wait_interrupt
        or encounter.blocked_reason
    ):
        raise ConflictError("Great Haste must obey the encounter turn and response pause")
    turn = TakeCombatTurn(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=command.expected_revision,
        encounter_id=encounter.id,
        maneuver="concentrate",
    )
    context = CombatContext(play, before)
    prepared, _, context = _prepare_command(before, turn, context)
    encounter = _prepare_encounter(prepared, turn, context)
    explosion_guard(prepared.resources)
    prepared, encounter = _validate_turn(
        prepared, turn, encounter, context, preserve_concentration=True
    )
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if actor.forced_do_nothing:
        raise ValidationError("Actor must take the required Do Nothing maneuver")
    origin = origins(before.resources).get(command.cast_id)
    if command.operation == "concentrate" and (
        origin is None
        or origin.encounter_id != encounter.id
        or origin.turn_index != encounter.turn_index
        or before.resources.game_time != origin.game_time + encounter.round - origin.round
    ):
        raise ConflictError("Great Haste requires consecutive subjective Concentrate opportunities")
    with combat_generation(frozenset({"maneuver-budget"})):
        encounter = begin(play.rules_context, before, encounter)
    state = prepared
    if first_opportunity(encounter):
        state = injury_turn(
            play.rules_context, state, command.actor_id, command.id, start=True, do_nothing=False
        )
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"revision": before.resources.revision}
                )
            }
        )
    with subjective_casting():
        state, receipt = apply_host(play.rules_context, state, command, combat_encounter=encounter)
    if command.operation == "start":
        resources = save(
            state.resources,
            PREFIX,
            command.cast_id,
            command.actor_id,
            CastingOrigin(
                cast_id=command.cast_id,
                encounter_id=encounter.id,
                round=encounter.round,
                turn_index=encounter.turn_index,
                game_time=before.resources.game_time,
            ),
        )
        state = state.model_copy(update={"resources": resources})
    if last_opportunity(encounter):
        state = injury_turn(
            play.rules_context, state, command.actor_id, command.id, start=False, do_nothing=False
        )
    resources = state.resources.model_copy(update={"revision": before.resources.revision + 1})
    state = state.model_copy(update={"resources": resources, "revision": before.revision + 1})
    engine = play.engine.combat
    assert engine is not None
    actor = prepare_commitment(
        engine,
        encounter,
        actor,
        resources,
        actor_id=command.actor_id,
        maneuver="concentrate",
        item_id=None,
        target_id=None,
        attack_option=None,
        defense_option=None,
        wait_trigger=None,
        second_item_id=None,
        second_target_id=None,
        second_mode_id=None,
        basic=encounter.spatial_kind == "basic",
    )
    actor = actor.model_copy(update={"last_maneuver": "concentrate", "last_attack_item_id": None})
    finished = engine._advance(engine._replace(encounter, actor))
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.great_haste_concentrate",
        round=finished.round,
        current_actor_id=finished.current_actor_id,
    )
    context = CombatContext(play, before)
    step = CombatStep(state, finished, resources, result)
    encounters = tuple(finished if e.id == encounter.id else e for e in state.encounters)
    step, encounters = _settle_combat(step, command, encounters, context)
    state, _ = _finish_combat(step, command, encounters, context, advance_revision=False)
    return state, receipt
