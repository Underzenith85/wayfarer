"""B66 fragment choices continue the actual B415 blast at one captured actor."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_visibility import (
    SecretAttackSource,
    conceal_attack_source,
)
from wayfarer.engine.simulation.combat.blast_phases import (
    FragmentContinuation,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.fragment_state import (
    active_fragment,
    fragment_attack_id,
    save_fragment,
)
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.combat.thrown.explosions import (
    BlastPhaseResult,
    amend_future_fragment_responses,
    continuing_fragment_responses,
    prepare_blast_fragments,
    resume_fragment_attack,
    validate_fragment_attack,
    validate_later_fragment_responses,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.luck import LuckRoll
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.encounters import _prepare_encounter
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.opponent_attack_luck import select_opponent_roll
from wayfarer.orchestration.opponent_attack_privacy import hide_secret_totals
from wayfarer.orchestration.opponent_fragment_records import (
    AmendFragmentResponses,
    ChooseOpponentFragment,
    OpponentFragmentPending,
    PrepareOpponentFragment,
    RecordedFragmentLaunch,
)
from wayfarer.orchestration.opponent_fragment_sources import validate_fragment_launch
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def _visible_source(pending: OpponentFragmentPending) -> SecretAttackSource:
    return SecretAttackSource(
        encounter_id=pending.resolution.encounter_id,
        attack_id=fragment_attack_id(pending.resolution.blast_id, pending.actor_id),
        attacker_id=pending.attacker_id,
        target_id=pending.actor_id,
    )


def _cursor(pending: OpponentFragmentPending) -> FragmentContinuation:
    return FragmentContinuation(
        launch=pending.launch,
        resolution=pending.resolution,
        preparation=pending.preparation,
        original=pending.original,
        secret=pending.secret,
    )


def _open_phase(
    runtime: RulesContext,
    result: BlastPhaseResult,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    source: PrepareOpponentFragment,
    launch: RecordedFragmentLaunch,
    command_id: str,
) -> tuple[PlayState, TaskSnapshot, OpponentFragmentPending]:
    preparation = result.pending
    assert preparation is not None
    state, encounter = reconcile_equipment(result.state, result.encounter)
    state = state.model_copy(
        update={
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters)
        }
    )
    original = None if source.secret else preparation.spec.roll(runtime.rng)
    pending = OpponentFragmentPending(
        id=identity("opponent-fragment-roll:", command_id + ":" + preparation.target.actor_id),
        actor_id=preparation.target.actor_id,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        launch=launch,
        resolution=source.resolution,
        preparation=preparation,
        original=original,
        secret=source.secret,
    )
    if any(roll.id == pending.id for roll in saved.luck.rolls):
        raise ConflictError("Fragment opportunity identity was already used")
    resources = save_fragment(state.resources, _cursor(pending), command_id + ":pending")
    if pending.secret:
        resources = conceal_attack_source(resources, _visible_source(pending), system=True)
    state = state.model_copy(update={"resources": resources})
    roll = LuckRoll(
        id=pending.id,
        actor_id=pending.attacker_id,
        kind="success",
        scope="attack",
        affected_actor_ids=(pending.actor_id,),
        original=original.dice if original else None,
        secret=pending.secret,
        task_class="weapon",
        failed=bool(original and not original.outcome.succeeded),
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
    return state, saved, pending


def _finish_phase(
    play: PlayService,
    before: PlayState,
    phase: BlastPhaseResult,
    source: PrepareOpponentFragment,
    command_id: str,
) -> tuple[PlayState, CombatResult]:
    response = source.resolution.model_copy(
        update={"id": command_id, "expected_revision": before.revision}
    )
    context = CombatContext(play, before)
    combat = CombatResult(
        encounter_id=phase.encounter.id,
        code="combat.weapon_explosion_resolved",
        round=phase.encounter.round,
        current_actor_id=phase.encounter.current_actor_id,
    )
    step = CombatStep(
        phase.state, phase.encounter, phase.state.resources, combat, phase.deferred_ticks
    )
    encounters = tuple(
        phase.encounter if e.id == phase.encounter.id else e for e in phase.state.encounters
    )
    step, encounters = _settle_combat(step, response, encounters, context)
    state, combat = _finish_combat(step, response, encounters, context)
    if source.secret:
        state = hide_secret_totals(state, phase.encounter.id, command_id)
    return play.checkpoint(state, before=before), combat


def _source_for(pending: OpponentFragmentPending) -> PrepareOpponentFragment:
    return PrepareOpponentFragment(
        id=pending.resolution.id,
        actor_id=pending.actor_id,
        expected_revision=pending.resolution.expected_revision,
        resolution=pending.resolution,
        launch_command_id=pending.launch.command.id,
        owner_ids=pending.preparation.progress.stop_for,
        secret=pending.secret,
        incendiary_objects=pending.preparation.progress.incendiary_objects,
        launch=pending.launch,
    )


def open_opponent_fragment(
    play: PlayService,
    state: PlayState,
    command: PrepareOpponentFragment,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    if saved.pending is not None:
        raise ConflictError("Another immediate Luck decision is pending")
    resolution = command.resolution
    encounter = encounter_for(state, resolution.encounter_id)
    blast = next((b for b in blasts(state.resources) if b.id == resolution.blast_id), None)
    if blast is None or blast.resolved:
        raise ConflictError("No unresolved source blast is available")
    owner_ids = command.owner_ids or (command.actor_id,)
    launch = validate_fragment_launch(state, blast, owner_ids, command.launch)
    previous = active_fragment(state.resources, blast.id)
    if previous is not None:
        if not previous.cancelled or previous.preparation is None:
            raise ConflictError("The blast already has an immediate fragment choice")
        if not command.secret or previous.launch != launch:
            raise ConflictError(
                "A resumed fragment choice cannot change its settled source or secrecy"
            )
        resumed = continuing_fragment_responses(
            play.rules_context, state, encounter, previous, resolution
        )
        remaining = resumed.progress.actor_ids[previous.preparation.progress.actor_index :]
        if set(owner_ids) - set(remaining) or previous.preparation.target.actor_id not in owner_ids:
            raise ConflictError("The new fragment plan must start at the current unresolved actor")
        phase = resumed.model_copy(
            update={"progress": resumed.progress.model_copy(update={"stop_for": owner_ids})}
        )
        command = command.model_copy(
            update={
                "resolution": previous.resolution.model_copy(
                    update={"responses": phase.progress.responses}
                )
            }
        )
        result = BlastPhaseResult(state, encounter, phase)
    else:
        if (resolution.id, resolution.expected_revision) != (command.id, state.revision):
            raise ValidationError("A new blast source must match this preparation command")
        if resolution.actor_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Blast source requires trusted director authority")
        state = play.checkpoint(state)
        state, _, context = _prepare_command(state, resolution, CombatContext(play, state))
        encounter = _prepare_encounter(state, resolution, context)
        result = prepare_blast_fragments(
            play.rules_context,
            state,
            encounter,
            blast_id=blast.id,
            command_id=resolution.id,
            responses=resolution.responses,
            object_cover=resolution.object_cover,
            object_sizes=resolution.object_sizes,
            center=resolution.center,
            environment=resolution.environment,
            contact_actor_id=resolution.contact_actor_id,
            internal_actor_id=resolution.internal_actor_id,
            stop_for=owner_ids,
            incendiary_objects=command.incendiary_objects,
        )
    if result.pending is None:
        state, combat = _finish_phase(play, state, result, command, command.id)
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="completed",
                secret=command.secret,
                combat_json=combat.model_dump_json(),
            ),
        )
    state, saved, pending = _open_phase(
        play.rules_context, result, saved, clock, command, launch, command.id
    )
    return (
        state,
        saved,
        TaskResult(
            command_id=command.id,
            actor_id=pending.actor_id,
            status="pending",
            pending_id=pending.id,
            check=pending.original,
            secret=pending.secret,
        ),
    )


def choose_opponent_fragment(
    play: PlayService,
    state: PlayState,
    command: ChooseOpponentFragment,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if not isinstance(pending, OpponentFragmentPending) or (pending.id, pending.actor_id) != (
        command.pending_id,
        command.actor_id,
    ):
        raise ConflictError("Fragment attack is no longer this owner's immediate choice")
    cursor = active_fragment(state.resources, pending.resolution.blast_id)
    if cursor != _cursor(pending):
        raise ConflictError("Fragment attack lost its exact canonical blast continuation")
    if command.choice == "cancel":
        if not pending.secret:
            raise ValidationError("A rolled fragment original cannot be cancelled")
        state = state.model_copy(
            update={
                "resources": save_fragment(
                    state.resources,
                    cursor.model_copy(update={"cancelled": True}),
                    command.id + ":cancel",
                )
            }
        )
        saved = saved.model_copy(
            update={
                "pending": None,
                "luck": saved.luck.model_copy(update={"pending_roll_id": None}),
            }
        )
        return (
            state,
            saved,
            clock,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="cancelled",
                secret=True,
            ),
        )
    encounter = encounter_for(state, pending.resolution.encounter_id)
    validate_fragment_attack(state, encounter, pending.preparation)
    validate_later_fragment_responses(play.rules_context, state, encounter, pending.preparation)
    saved, clock, _, selected, receipt = select_opponent_roll(
        play, state, command, saved, clock, pending
    )
    encounter = encounter_for(state, pending.resolution.encounter_id)
    phase = resume_fragment_attack(
        play.rules_context, state, encounter, pending.preparation, selected
    )
    source = _source_for(pending)
    combat_json = None
    if phase.pending is not None:
        state, saved, _ = _open_phase(
            play.rules_context, phase, saved, clock, source, pending.launch, command.id
        )
    else:
        state, combat = _finish_phase(play, state, phase, source, command.id)
        state = state.model_copy(
            update={
                "resources": save_fragment(
                    state.resources,
                    cursor.model_copy(
                        update={
                            "preparation": None,
                            "original": None,
                        }
                    ),
                    command.id + ":complete",
                )
            }
        )
        combat_json = combat.model_dump_json()
    return (
        state,
        saved,
        clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            check=selected,
            luck=receipt,
            secret=pending.secret,
            combat_json=combat_json,
        ),
    )


def amend_fragment_responses_task(
    play: PlayService,
    state: PlayState,
    command: AmendFragmentResponses,
    saved: TaskSnapshot,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    pending = saved.pending
    if not isinstance(pending, OpponentFragmentPending) or pending.id != command.pending_id:
        raise ConflictError("No matching fragment choice remains for this source amendment")
    if active_fragment(state.resources, pending.resolution.blast_id) != _cursor(pending):
        raise ConflictError("Fragment amendment lost its exact canonical continuation")
    prepared = amend_future_fragment_responses(
        play.rules_context,
        state,
        encounter_for(state, pending.resolution.encounter_id),
        pending.preparation,
        command.responses,
    )
    pending = pending.model_copy(
        update={
            "preparation": prepared,
            "resolution": pending.resolution.model_copy(
                update={"responses": prepared.progress.responses}
            ),
        }
    )
    state = state.model_copy(
        update={
            "resources": save_fragment(state.resources, _cursor(pending), command.id + ":amend")
        }
    )
    return (
        state,
        saved.model_copy(update={"pending": pending}),
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            secret=pending.secret,
        ),
    )
