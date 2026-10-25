"""No-dice validation of one tactical command, shared by both legal-choice enumerators."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.attack_policy import bind_pending, uses_basic_set_attacks
from wayfarer.engine.simulation.combat.attack_preparation import project_refund
from wayfarer.engine.simulation.combat.attack_targets import require_pending_minimum
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.maneuver_budget import first_opportunity
from wayfarer.engine.simulation.combat.maneuver_transitions import observe
from wayfarer.engine.simulation.combat.melee.attack import prepare_attack
from wayfarer.engine.simulation.combat.melee.defense import validate_defense_choices
from wayfarer.engine.simulation.combat.melee.modes import mode, mode_reach
from wayfarer.engine.simulation.combat.ranged.situation import validate_command
from wayfarer.engine.simulation.combat.ranged.spraying import prepare_spraying_fire
from wayfarer.engine.simulation.combat.ranged_target_policy import (
    bind_pending as bind_ranged_pending,
)
from wayfarer.engine.simulation.combat.tactical_transitions import prepare_defense
from wayfarer.engine.simulation.combat.thrown.items import validate_catch
from wayfarer.engine.simulation.combat.unarmed.attack import preflight_unarmed
from wayfarer.engine.simulation.combat.unarmed.declaration import validate_action
from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense
from wayfarer.engine.simulation.combat.unarmed.fighters import guard_control
from wayfarer.engine.simulation.equipment.catalog import MeleeMode
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.errors import ValidationError
from wayfarer.orchestration import defense_posture_generations
from wayfarer.orchestration.combat import (
    ChooseDefense,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TakeUnarmedTurn,
    generations,
)
from wayfarer.orchestration.defense_posture_generations import defense_posture_scope
from wayfarer.orchestration.play import PlayService


@contextmanager
def _fresh_generations() -> Iterator[None]:
    with combat_generation(generations.ACTIVE):
        yield


@_fresh_generations()
def preview(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn | TakeUnarmedTurn | ChooseDefense | ResumeInterruptedTurn,
) -> None:
    """Pure validation only: no execute, dice, mutation, or provisional receipts."""
    engine = play.engine.combat
    assert engine is not None
    guard(state, command.actor_id, command.kind)
    affected = {command.actor_id}
    if isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)) and command.target_id is not None:
        affected.add(command.target_id)
    if isinstance(command, ChooseDefense):
        pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
        if pending:
            affected.update((pending.attacker_id, pending.defender_id))
        if unarmed:
            affected.update((unarmed.actor_id, unarmed.target_id))
    require_settled(state.resources.recovery_tasks, frozenset(affected), state.resources.game_time)
    affected.update(g.target_id for g in encounter.grips)
    require_hazards_settled(state.resources.hazards, frozenset(affected), state.resources.game_time)
    if isinstance(command, ResumeInterruptedTurn):
        interrupt = encounter.wait_interrupt
        if interrupt is None or not interrupt.ready or interrupt.actor_id != command.actor_id:
            raise ValidationError("No interrupted turn is ready")
        return
    guard_control(encounter, command, state)
    if isinstance(command, ChooseDefense):
        # Choices describe a fresh command, not a recorded retry.
        with (
            combat_generation(generations.ACTIVE),
            defense_posture_scope(defense_posture_generations.ACTIVE),
        ):
            require_pending_minimum(play.rules_context, state, encounter)
            if command.catch_thrown:
                validate_catch(play.rules_context, state, encounter, command)
            prepared = prepare_defense(play.rules_context, state, encounter, command)
            if prepared.pending_unarmed is not None:
                unarmed_defense(
                    play.rules_context,
                    state,
                    prepared,
                    command.actor_id,
                    command.defense,
                    command.item_id,
                )
            else:
                if (
                    prepared.pending_defense is None
                    or prepared.pending_defense.defender_id != command.actor_id
                ):
                    raise ValidationError("Defense is unavailable")
                validate_defense_choices(
                    play.rules_context,
                    state,
                    prepared,
                    command.defense,
                    command.item_id,
                    command.second_defense,
                    command.second_item_id,
                )
            return
    if isinstance(command, TakeUnarmedTurn):
        validate_action(play.rules_context, state, encounter, command)
        preflight_unarmed(play.rules_context, state, encounter, command)
        return
    state = project_refund(
        state,
        command.actor_id,
        starting=uses_basic_set_attacks(play.rules_context)
        and encounter.wait_interrupt is None
        and first_opportunity(encounter),
    )
    hp = next(p for p in state.resources.pools if p.id == f"hp:{command.actor_id}")
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    if actor.conditions or (hp.injury and hp.injury.incapacitated):
        raise ValidationError("Actor is unavailable")
    if (hp.injury and hp.injury.stunned) and command.maneuver != "do_nothing":
        raise ValidationError("Actor must recover from stun")
    validate_command(play.rules_context, state, encounter, command)
    actor_state = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if actor_state.forced_do_nothing and command.maneuver != "do_nothing":
        raise ValidationError("Actor must do nothing")
    encounter = engine._replace(
        encounter,
        actor_state.model_copy(
            update={"movement_allowance": movement(play.rules_context, state, command.actor_id)}
        ),
    )
    if command.item_id and command.maneuver in (
        "attack",
        "all_out_attack",
        "move_and_attack",
        "feint",
    ):
        selected = mode(
            play.rules_context, state, command.actor_id, command.item_id, command.mode_id
        )
        if isinstance(selected, MeleeMode):
            encounter = engine._replace(
                encounter,
                next(
                    p for p in encounter.participants if p.actor_id == command.actor_id
                ).model_copy(update={"reach": mode_reach(selected)}),
            )
    result, projected_resources, _ = engine.take_turn(
        encounter,
        actor_id=command.actor_id,
        maneuver=command.maneuver,
        resources=state.resources,
        command_id=command.id,
        destination=command.destination,
        facing=command.facing,
        item_id=command.item_id,
        target_id=command.target_id,
        posture=command.posture,
        attack_option=command.attack_option,
        defense_option=command.defense_option,
        wait_trigger=command.wait_trigger,
        step_timing=command.step_timing,
        second_item_id=command.second_item_id,
        second_target_id=command.second_target_id,
        second_mode_id=command.second_mode_id,
        hex_path=command.hex_path,
        hex_facing=command.hex_facing,
        pop_up=command.pop_up,
        enter_high_speed=command.enter_high_speed,
        basic_move=command.basic_move,
        spatial_revision=command.expected_revision + 1 if command.basic_move is not None else None,
    )
    if result.pending_defense is not None:
        prepared = prepare_attack(
            play.rules_context,
            state.model_copy(update={"resources": projected_resources}),
            result,
            command.mode_id,
            hit_location=command.hit_location,
            armor_chink=command.armor_chink,
            strike_strength=command.strike_strength,
            subdual_mode=command.subdual_mode,
            target_item_id=command.target_item_id,
            cover_item_id=command.cover_item_id,
            overpenetration_target_id=command.overpenetration_target_id,
            area_aim_point=command.area_aim_point,
            scatter_squared=command.scatter_squared,
            shots=command.shots,
            preview=True,
        )
        prepared = prepare_spraying_fire(play.rules_context, state, prepared, command)
        candidate = bind_pending(
            play.rules_context,
            state.model_copy(update={"resources": projected_resources}),
            prepared,
        )
        candidate = bind_ranged_pending(play.rules_context, candidate, prepared)
        require_pending_minimum(play.rules_context, candidate, prepared)
    if command.maneuver == "aim":
        observe(play.rules_context, state, result, command)
