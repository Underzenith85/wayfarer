"""Unarmed strike Luck resumes current injury and canonical sequence settlement."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.attack_roll import AttackRollChoice
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.combat.unarmed.attack import finish_unarmed_response
from wayfarer.engine.simulation.combat.unarmed.damage_records import (
    ArmedParryDamageStage,
    UnarmedDamageStage,
)
from wayfarer.engine.simulation.combat.unarmed.injury import finish_armed_parry_damage
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedTrace
from wayfarer.engine.simulation.combat.unarmed.resolution import defend, finish_unarmed_damage
from wayfarer.engine.simulation.traits.luck import LuckRoll
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.encounters import _prepare_encounter
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.damage_settlement import settle_damage_response
from wayfarer.orchestration.owner_damage_luck import select_owner_damage
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    PrepareOwnerDamage,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity
from wayfarer.orchestration.unarmed_damage_records import (
    ArmedParryDamagePending,
    UnarmedDamagePending,
)


def _complete(
    play: PlayService,
    before: PlayState,
    state: PlayState,
    encounter: Encounter,
    response: ChooseDefense,
    trace: UnarmedTrace,
    *,
    reacting: bool,
    secret: bool,
    captured_attacker: ValidatedBuild | None,
    damage_dice: tuple[int, ...] | None = None,
) -> tuple[PlayState, OwnerDamageOutcome]:
    response = response.model_copy(update={"expected_revision": before.revision})
    state, encounter, combat = finish_unarmed_response(
        play.rules_context,
        state,
        encounter,
        response,
        trace,
        reacting=reacting,
        captured_attacker=captured_attacker,
    )
    step = CombatStep(state, encounter, state.resources, combat)
    state, combat = settle_damage_response(play, before, step, response, secret=secret)
    return state, OwnerDamageOutcome(
        dice=trace.damage_dice if damage_dice is None else damage_dice, combat=combat
    )


def open_unarmed_damage(
    play: PlayService,
    state: PlayState,
    command: PrepareOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    *,
    selected_attack: AttackRollChoice | None = None,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    response = command.response
    if not isinstance(response, ChooseDefense):
        raise ValidationError("Unarmed damage requires its target's actual defense")
    state = play.checkpoint(state)
    before = state
    context = CombatContext(play, state, selected_attack=selected_attack)
    state, prepared_response, context = _prepare_command(state, response, context)
    assert isinstance(prepared_response, ChooseDefense)
    response = prepared_response
    encounter = _prepare_encounter(state, response, context)
    original = encounter.pending_unarmed
    if original is None:
        raise ConflictError("Unarmed delivery is no longer pending")
    reacting = encounter.wait_interrupt is not None
    resolved = defend(
        context.attack_runtime,
        state,
        encounter,
        response,
        selected_attack=selected_attack.selected if selected_attack else None,
        prepare_damage=True,
        secret_damage=command.secret,
    )
    if isinstance(resolved, tuple):
        updated, encounter, trace = resolved
        state, outcome = _complete(
            play,
            before,
            updated,
            encounter,
            response,
            trace,
            reacting=reacting,
            secret=command.secret,
            captured_attacker=selected_attack.captured_attacker if selected_attack else None,
        )
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=original.actor_id,
                status="completed",
                secret=command.secret,
                damage_json=outcome.model_dump_json(),
            ),
        )
    assert isinstance(resolved, (UnarmedDamageStage, ArmedParryDamageStage))
    state, encounter = reconcile_equipment(resolved.state, resolved.encounter)
    state = state.model_copy(
        update={
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters)
        }
    )
    pending: UnarmedDamagePending | ArmedParryDamagePending
    if isinstance(resolved, ArmedParryDamageStage):
        pending = ArmedParryDamagePending(
            id=identity("owner-damage:", command.id),
            actor_id=resolved.preparation.inputs.actor_id,
            encounter_id=encounter.id,
            opened_elapsed_microseconds=clock.elapsed_microseconds,
            preparation=resolved.preparation,
            response=response,
            reacting=reacting,
        )
    else:
        pending = UnarmedDamagePending(
            id=identity("owner-damage:", command.id),
            actor_id=original.actor_id,
            encounter_id=encounter.id,
            opened_elapsed_microseconds=clock.elapsed_microseconds,
            preparation=resolved.preparation,
            response=response,
            reacting=reacting,
        )
    if any(roll.id == pending.id for roll in saved.luck.rolls):
        raise ConflictError("Unarmed damage original identity was already used")
    roll = LuckRoll(
        id=pending.id,
        actor_id=pending.actor_id,
        kind="damage",
        dice_count=pending.preparation.dice_count,
        modifier=pending.modifier,
        original=pending.original,
        secret=pending.secret,
    )
    saved = saved.model_copy(
        update={
            "pending": pending,
            "luck": saved.luck.model_copy(
                update={
                    "revision": state.revision,
                    "game_time": state.resources.game_time,
                    "rolls": saved.luck.rolls + (roll,),
                    "pending_roll_id": pending.id,
                }
            ),
        }
    )
    return (
        state,
        saved,
        TaskResult(
            command_id=command.id,
            actor_id=pending.actor_id,
            status="pending",
            pending_id=pending.id,
            secret=pending.secret,
            damage_json=pending.original_json,
        ),
    )


def choose_unarmed_damage(
    play: PlayService,
    state: PlayState,
    command: ChooseOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if not isinstance(pending, (UnarmedDamagePending, ArmedParryDamagePending)) or (
        pending.id,
        pending.actor_id,
    ) != (
        command.pending_id,
        command.actor_id,
    ):
        raise ConflictError("Unarmed damage is no longer this owner's immediate choice")
    encounter = encounter_for(state, pending.encounter_id)
    if encounter.pending_unarmed != pending.preparation.inputs.pending:
        raise ConflictError("Captured unarmed damage delivery identity changed")
    target_id = (
        pending.preparation.inputs.target_id
        if isinstance(pending, ArmedParryDamagePending)
        else pending.preparation.inputs.pending.target_id
    )
    build(play.rules_context, state, target_id)
    saved, clock, receipt, dice = select_owner_damage(play, state, command, saved, clock, pending)
    if isinstance(pending, ArmedParryDamagePending):
        updated, encounter, selected = finish_armed_parry_damage(
            play.rules_context, state, encounter, pending.preparation.inputs, selected_damage=dice
        )
        trace = pending.preparation.trace.model_copy(update={"effect_dice": selected})
    else:
        updated, encounter, trace = finish_unarmed_damage(
            play.rules_context, state, encounter, pending.preparation.inputs, selected_damage=dice
        )
    state, outcome = _complete(
        play,
        state,
        updated,
        encounter,
        pending.response,
        trace,
        reacting=pending.reacting,
        secret=pending.secret,
        captured_attacker=pending.preparation.captured_attacker
        if isinstance(pending, ArmedParryDamagePending)
        else pending.preparation.inputs.captured_attacker,
        damage_dice=dice if isinstance(pending, ArmedParryDamagePending) else None,
    )
    return (
        state,
        saved,
        clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            luck=receipt,
            secret=pending.secret,
            damage_json=outcome.model_dump_json(),
        ),
    )
