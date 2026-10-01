"""Normalising a command before any step sees it."""

from __future__ import annotations

import json
from dataclasses import replace

from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    EndEncounter,
    JoinEncounter,
    RepairEquipment,
    ResolveChokeEffects,
    ResumeInterruptedTurn,
    RetrieveEquipment,
    SalvageEquipment,
    StartBasicEncounter,
    StartEncounter,
    TakeCombatTurn,
    TakeUnarmedTurn,
    TypedCombatCommand,
    WithdrawEncounter,
)
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.combat.sensory_combat import blind_hit_location
from wayfarer.engine.simulation.health.fright import maneuver_allowed
from wayfarer.engine.simulation.health.fright_state import can_defend
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, encounter_for
from wayfarer.orchestration.recovery import guard


def _require_supported_resume(state: PlayState, command: ResumeInterruptedTurn) -> None:
    paused = encounter_for(state, command.encounter_id)
    mover = next(p for p in paused.participants if p.actor_id == command.actor_id)
    if command.cancel and mover.high_speed and mover.high_speed.remaining_yards:
        raise ValidationError("Cancelling high-speed movement requires the B395 braking rules")


def _retire_disabled_trajectory(
    state: PlayState, saved: TakeCombatTurn | TakeUnarmedTurn
) -> TakeCombatTurn | TakeUnarmedTurn:
    """B385: a knocked-down or incapacitated mover cannot spend its unused path."""
    encounter = encounter_for(state, saved.encounter_id)
    mover = next(p for p in encounter.participants if p.actor_id == saved.actor_id)
    if not mover.high_speed or not mover.high_speed.remaining_yards:
        return saved
    hp = next(p for p in state.resources.pools if p.id == f"hp:{saved.actor_id}")
    actor = next(a for a in state.actors if a.actor_id == saved.actor_id)
    if not (
        mover.posture != "standing"
        or actor.conditions
        or hp.injury is not None
        and (hp.injury.incapacitated or hp.injury.stunned)
    ):
        return saved
    return TakeCombatTurn(
        id=saved.id,
        actor_id=saved.actor_id,
        expected_revision=saved.expected_revision,
        encounter_id=saved.encounter_id,
        maneuver="do_nothing",
    )


def _normalize_sensory_location(
    state: PlayState, command: TypedCombatCommand
) -> TypedCombatCommand:
    """Random location is a source consequence, without expanding public inputs."""
    if (
        isinstance(command, TakeCombatTurn)
        and command.suppression_zones
        and acute_blindness(state.resources, command.actor_id)
    ):
        raise ValidationError(
            "Blind suppression fire requires a supported nonvisual area-fire consumer"
        )
    if (
        isinstance(command, TakeCombatTurn)
        and command.maneuver in ATTACK_MANEUVERS
        and command.attack_option != "feint"
    ):
        command = command.model_copy(
            update={
                "hit_location": blind_hit_location(
                    state,
                    command.actor_id,
                    command.hit_location,
                    target_item_id=command.target_item_id,
                    armor_chink=command.armor_chink,
                )
            }
        )
    return command


def _require_voluntary_equipment(state: PlayState, command: TypedCombatCommand) -> None:
    if isinstance(command, WithdrawEncounter) or (
        isinstance(command, (RetrieveEquipment, RepairEquipment, SalvageEquipment))
        and command.stage != "cancel"
    ):
        require_innate_actor_action(state, command.actor_id)


def _prepare_command(
    state: PlayState, command: TypedCombatCommand, context: CombatContext
) -> tuple[PlayState, TypedCombatCommand, CombatContext]:
    _require_voluntary_equipment(state, command)
    engine = context.engine
    resuming = context.resuming
    reaction = context.reaction
    if isinstance(command, EndEncounter):
        if any(
            not b.resolved and b.encounter_id == command.encounter_id
            for b in blasts(state.resources)
        ):
            raise ConflictError("Resolve armed explosives before ending the encounter")
    resuming = False
    reaction = False
    movement_checkpoint = False
    if isinstance(command, ResumeInterruptedTurn):
        paused = encounter_for(state, command.encounter_id)
        interrupt = paused.wait_interrupt
        if interrupt is None or not interrupt.ready or interrupt.actor_id != command.actor_id:
            raise ConflictError("No interrupted turn is ready for this actor")
        _require_supported_resume(state, command)
        # An interrupted unarmed turn had not begun when it paused, so it resumes
        # whole instead of replaying turn bookkeeping the armed path already spent.
        unarmed_turn = json.loads(interrupt.command_json)["kind"] == "take_unarmed_turn"
        saved: TakeCombatTurn | TakeUnarmedTurn
        if unarmed_turn:
            saved = (
                TakeCombatTurn(
                    id=command.id,
                    actor_id=command.actor_id,
                    expected_revision=command.expected_revision,
                    encounter_id=command.encounter_id,
                    maneuver="do_nothing",
                )
                if command.cancel
                else TakeUnarmedTurn.model_validate_json(interrupt.command_json)
            )
        else:
            saved = TakeCombatTurn.model_validate_json(interrupt.command_json)
        if command.cancel and not unarmed_turn:
            assert isinstance(saved, TakeCombatTurn)
            saved = saved.model_copy(
                update={
                    "maneuver": "do_nothing",
                    "shots": 1,
                    "reload_ammunition_id": None,
                    "unload_ammunition": False,
                    "fast_draw": False,
                    "cocking_aid_id": None,
                    "let_down_bow": False,
                    "recover_thrown_item": False,
                    "firearm_service": None,
                    "firearm_service_skill": "weapon",
                    "destination": None,
                    "hex_path": (),
                    "hex_facing": None,
                    "facing": None,
                    "posture": None,
                    "crouch": None,
                    "item_id": None,
                    "target_id": None,
                    "mode_id": None,
                    "attack_option": None,
                    "defense_option": None,
                    "wait_trigger": None,
                    "step_timing": "before",
                    "second_item_id": None,
                    "second_target_id": None,
                    "second_mode_id": None,
                    "braced": False,
                    "area_aim_point": None,
                    "scatter_squared": False,
                }
            )
        mover = next(p for p in paused.participants if p.actor_id == command.actor_id)
        movement_checkpoint = bool(
            interrupt.declaration.action == "move"
            and mover.high_speed is not None
            and mover.high_speed.remaining_yards is None
        )
        saved = _retire_disabled_trajectory(state, saved)
        command = saved.model_copy(
            update={"id": command.id, "expected_revision": command.expected_revision}
        )
        state = state.model_copy(
            update={
                "encounters": tuple(
                    e.model_copy(update={"wait_interrupt": None}) if e.id == paused.id else e
                    for e in state.encounters
                )
            }
        )
        resuming = not unarmed_turn
    elif isinstance(command, TakeCombatTurn):
        paused = encounter_for(state, command.encounter_id)
        reaction = (
            paused.wait_interrupt is not None
            and paused.wait_interrupt.waiter_id == command.actor_id
        )
        if (
            reaction
            and paused.wait_interrupt is not None
            and command.maneuver != "do_nothing"
            and command.mode_id != paused.wait_interrupt.declaration.mode_id
        ):
            raise ValidationError("Wait reaction must use the declared weapon mode")

    command = _normalize_sensory_location(state, command)
    if isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)) and not maneuver_allowed(
        state.resources, command.actor_id, command.maneuver
    ):
        raise ValidationError("This fright condition does not permit that maneuver")
    guard(
        state,
        command.actor_id,
        command.kind,
        allow_fright=(
            isinstance(command, TakeCombatTurn)
            and maneuver_allowed(state.resources, command.actor_id, command.maneuver)
            or isinstance(command, ChooseDefense)
            and (command.defense == "none" or can_defend(state.resources, command.actor_id))
        ),
    )
    if engine.rules.gurps_equipment is not None:
        affected = {command.actor_id}
        if isinstance(command, StartEncounter):
            affected.update(p.actor_id for p in command.placements)
        elif isinstance(command, StartBasicEncounter):
            affected.update(command.participant_ids)
        elif isinstance(command, JoinEncounter) and command.joining_actor_id is not None:
            affected.add(command.joining_actor_id)
        elif (
            isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)) and command.target_id is not None
        ):
            affected.add(command.target_id)
        elif isinstance(command, ChooseDefense):
            selected_encounter = encounter_for(state, command.encounter_id)
            if selected_encounter.pending_unarmed is not None:
                affected.update(
                    (
                        selected_encounter.pending_unarmed.actor_id,
                        selected_encounter.pending_unarmed.target_id,
                    )
                )
            pending = selected_encounter.pending_defense
            if pending is not None:
                affected.update((pending.attacker_id, pending.defender_id))
        require_settled(
            state.resources.recovery_tasks, frozenset(affected), state.resources.game_time
        )
        if not isinstance(command, ResolveChokeEffects):
            for active_encounter in state.encounters:
                if command.actor_id in active_encounter.turn_order:
                    affected.update(g.target_id for g in active_encounter.grips)
            require_hazards_settled(
                state.resources.hazards, frozenset(affected), state.resources.game_time
            )
    if command.expected_revision != state.revision:
        raise ConflictError("Play revision changed")
    return (
        state,
        command,
        replace(
            context, resuming=resuming, reaction=reaction, movement_checkpoint=movement_checkpoint
        ),
    )
