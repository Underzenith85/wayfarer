"""Unrolled B66 secret Inspect/Social choices within the task host transaction."""

from pydantic import TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace, evaluate_success
from wayfarer.engine.simulation.action_engine.engine import TaskCheckPreparation
from wayfarer.engine.simulation.actions import Inspect, PlayState, Social
from wayfarer.engine.simulation.events import action_result
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckRoll, apply_luck, luck_cooldown
from wayfarer.errors import ConflictError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock, spend_real_play_cooldown
from wayfarer.orchestration.task_context import approved, ready
from wayfarer.orchestration.task_records import (
    ChooseSecretTaskCheck,
    PrepareSecretTaskCheck,
    SecretTaskPending,
    TaskResult,
    TaskSnapshot,
    identity,
)

PREPARATION: TypeAdapter[TaskCheckPreparation] = TypeAdapter(TaskCheckPreparation)


def open_secret(
    state: PlayState,
    command: PrepareSecretTaskCheck,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    ordinary: Inspect | Social,
    prepared: TaskCheckPreparation,
) -> tuple[TaskSnapshot, TaskResult]:
    pending = SecretTaskPending(
        id=identity("secret-task-check:", command.id),
        actor_id=command.actor_id,
        prepared_elapsed_microseconds=clock.elapsed_microseconds,
        ordinary=ordinary,
        preparation_json=PREPARATION.dump_json(prepared).decode(),
    )
    if saved.pending is not None or any(roll.id == pending.id for roll in saved.luck.rolls):
        raise ConflictError("Another task choice is pending or its identity was already used")
    roll = LuckRoll(
        id=pending.id,
        actor_id=pending.actor_id,
        secret=True,
        task_class="social" if isinstance(ordinary, Social) else "other",
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
    return saved, TaskResult(
        command_id=command.id,
        actor_id=command.actor_id,
        status="pending",
        pending_id=pending.id,
        secret=True,
    )


def _finish(
    saved: TaskSnapshot, pending: SecretTaskPending, chosen: CheckTrace | None
) -> TaskSnapshot:
    """Keep cancelled identities in history, without inventing rolled dice."""
    luck = saved.luck.model_copy(
        update={
            "pending_roll_id": None,
            "rolls": tuple(
                roll.model_copy(update={"chosen_dice": chosen.dice, "chosen_total": chosen.total})
                if roll.id == pending.id and chosen is not None
                else roll
                for roll in saved.luck.rolls
            ),
        }
    )
    return saved.model_copy(update={"pending": None, "luck": luck})


def choose_secret(
    play: PlayService,
    state: PlayState,
    command: ChooseSecretTaskCheck,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if (
        not isinstance(pending, SecretTaskPending)
        or pending.id != command.pending_id
        or pending.actor_id != command.actor_id
    ):
        raise ConflictError("Secret task is no longer the immediate unrolled choice")
    if command.choice == "cancel":
        return (
            state,
            _finish(saved, pending, None),
            clock,
            TaskResult(
                command_id=command.id, actor_id=command.actor_id, status="cancelled", secret=True
            ),
        )
    ready(play, state, command.actor_id)
    ordinary = pending.ordinary.model_copy(
        update={"id": command.id, "expected_revision": state.revision}
    )
    prepared = PREPARATION.validate_json(pending.preparation_json)
    current = play.engine.prepare_task_check(state, ordinary, fatigue_already_paid=True)
    if current != prepared:
        raise ConflictError("Secret task context changed before its dice; cancel and prepare again")
    receipt = None
    if command.choice == "use-luck":
        build = approved(play, state, command.actor_id)
        definitions = play.engine.reviewer.compiler.definitions
        # No original exists to inspect. Eligibility is at this declaration,
        # including opportunities prepared before the previous use expired.
        clock = spend_real_play_cooldown(
            clock, actor_id=command.actor_id, seconds=luck_cooldown(build, definitions)
        )
        luck, receipt = apply_luck(
            saved.luck.model_copy(
                update={"revision": state.revision, "game_time": state.resources.game_time}
            ),
            LuckCommand(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                roll_id=pending.id,
            ),
            build,
            definitions,
            real_time=clock.elapsed_seconds,
            rng=play.rng,
            authorized_actor_id=command.actor_id,
            system=True,
        )
        dice = receipt.attempts[receipt.chosen_index]
        chosen = evaluate_success(
            prepared.target,
            prepared.modifiers,
            (dice[0], dice[1], dice[2]),
            rules_package=prepared.rules_package,
            rules_version=prepared.rules_version,
            rule_id="check:success",
        )
        saved = saved.model_copy(update={"pending": None, "luck": luck})
    else:
        chosen = prepared.check(rng=play.rng)
        saved = _finish(saved, pending, chosen)
    state, events = play.engine.resolve_prepared_task(
        state, ordinary, prepared, chosen, fatigue_already_paid=True
    )
    state = play.checkpoint(state.model_copy(update={"last_result": None}))
    return (
        state,
        saved,
        clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            check=chosen,
            luck=receipt,
            action=action_result(events),
            secret=True,
        ),
    )
