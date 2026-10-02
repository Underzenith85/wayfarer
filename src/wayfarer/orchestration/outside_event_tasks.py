"""B66 environmental choices resume canonical injury only after dice selection."""

import json

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.encounter_context import activity_for
from wayfarer.engine.simulation.health.hazard_damage import (
    prepare_hazard_damage,
)
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckRoll, apply_luck, luck_cooldown
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.outside_event_effects import resume_outside_damage
from wayfarer.orchestration.outside_event_prerequisites import settle_prerequisite
from wayfarer.orchestration.outside_event_records import (
    ChooseOutsideEvent,
    OutsideEventPending,
    PrepareOutsideEvent,
)
from wayfarer.orchestration.outside_event_sources import validate_natural_exposure
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock, spend_real_play_cooldown
from wayfarer.orchestration.task_context import approved
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def _actor_context(play: PlayService, state: PlayState, actor_id: str) -> str:
    approved(play, state, actor_id)
    if activity_for(state, actor_id).encounter is not None:
        raise ConflictError("Outside environmental choices currently require noncombat play")
    actor = next(actor for actor in state.actors if actor.actor_id == actor_id)
    entity = next(entity for entity in state.world.entities if entity.id == actor_id)
    return json.dumps([actor.model_dump_json(), entity.location_id], separators=(",", ":"))


def open_outside_event(
    play: PlayService,
    state: PlayState,
    command: PrepareOutsideEvent,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    if saved.pending is not None:
        raise ConflictError("Another immediate Luck choice is pending")
    initial = prepare_hazard_damage(
        state.resources, actor_id=command.actor_id, schedule_id=command.schedule_id
    )
    validate_natural_exposure(
        state, command.actor_id, initial.schedule, command.source, command.exposure_command
    )
    assert command.exposure_command is not None
    _actor_context(play, state, command.actor_id)
    # Settle existing checkpoint work before drawing the new original, never after.
    state = play.checkpoint(state)
    try:
        preparation = prepare_hazard_damage(
            state.resources, actor_id=command.actor_id, schedule_id=command.schedule_id
        )
        actor_json = _actor_context(play, state, command.actor_id)
        validate_natural_exposure(
            state, command.actor_id, preparation.schedule, command.source, command.exposure_command
        )
        entity = next(entity for entity in state.world.entities if entity.id == command.actor_id)
        if entity.location_id != preparation.schedule.spec.scene_id:
            raise ConflictError(
                "Outside environmental damage is not at the owner's current location"
            )
    except ValidationError, ConflictError:
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="interrupted",
                secret=command.secret,
                reason="Environmental context changed before the outside roll",
            ),
        )
    state, preparation = settle_prerequisite(play.rules_context, state, command, preparation)
    if not preparation.dice_count:
        before_effect = state
        state, outcome = resume_outside_damage(
            play.rules_context,
            state,
            command.id,
            preparation,
            (),
            secret=command.secret,
            exposure_command_id=command.exposure_command.id,
        )
        state = play.checkpoint(state, before=before_effect)
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="completed",
                check=preparation.resistance,
                secret=command.secret,
                outside_event_json=outcome.model_dump_json(),
            ),
        )
    pending_id = identity("outside-event:", command.id)
    if any(roll.id == pending_id for roll in saved.luck.rolls):
        raise ConflictError("Outside event identity was already used")
    original = None if command.secret else draw_dice(play.rng, preparation.dice_count)
    pending = OutsideEventPending(
        id=pending_id,
        actor_id=command.actor_id,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        secret=command.secret,
        original=original,
        preparation=preparation,
        actor_json=actor_json,
        source=command.source,
        exposure_command=command.exposure_command,
    )
    roll = LuckRoll(
        id=pending.id,
        actor_id=command.actor_id,
        kind="hazard-damage",
        dice_count=preparation.dice_count,
        modifier=preparation.modifier,
        original=original,
        scope="party-event",
        affected_actor_ids=(command.actor_id,),
        secret=command.secret,
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
            actor_id=command.actor_id,
            status="pending",
            pending_id=pending.id,
            secret=command.secret,
            outside_event_json=pending.original_json,
        ),
    )


def _finish(
    saved: TaskSnapshot, pending: OutsideEventPending, dice: tuple[int, ...] | None
) -> TaskSnapshot:
    return saved.model_copy(
        update={
            "pending": None,
            "luck": saved.luck.model_copy(
                update={
                    "pending_roll_id": None,
                    "rolls": tuple(
                        roll.model_copy(
                            update={
                                "chosen_dice": dice,
                                "chosen_total": max(1, sum(dice) + roll.modifier),
                            }
                        )
                        if roll.id == pending.id and dice is not None
                        else roll
                        for roll in saved.luck.rolls
                    ),
                }
            ),
        }
    )


def choose_outside_event(
    play: PlayService,
    state: PlayState,
    command: ChooseOutsideEvent,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if (
        not isinstance(pending, OutsideEventPending)
        or pending.id != command.pending_id
        or pending.actor_id != command.actor_id
    ):
        raise ConflictError("Outside event is no longer the immediate pending choice")
    if command.choice == "cancel":
        if not pending.secret:
            raise ValidationError("An already rolled outside event must be resolved")
        return (
            state,
            _finish(saved, pending, None),
            clock,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="cancelled",
                secret=True,
            ),
        )
    current = prepare_hazard_damage(
        state.resources,
        actor_id=command.actor_id,
        schedule_id=pending.preparation.schedule.id,
        resistance=pending.preparation.resistance,
    )
    if (
        current != pending.preparation
        or _actor_context(play, state, command.actor_id) != pending.actor_json
    ):
        raise ConflictError("Outside event context changed before selection")
    validate_natural_exposure(
        state, command.actor_id, current.schedule, pending.source, pending.exposure_command
    )
    receipt = None
    dice = pending.original
    if command.choice == "use-luck":
        build = approved(play, state, command.actor_id)
        if any(
            purchase.definition_id == "trait:advantage:luck"
            and purchase.trait is not None
            and purchase.trait.modifiers
            for purchase in build.trait_purchases
        ):
            raise ValidationError("Outside events currently require unmodified Luck")
        definitions = play.engine.reviewer.compiler.definitions
        previous = next(
            (entry for entry in clock.cooldowns if entry.actor_id == command.actor_id), None
        )
        if (
            dice is not None
            and previous is not None
            and pending.opened_elapsed_microseconds < previous.available_at_microseconds
        ):
            raise ValidationError("Luck was cooling down when this outside original was rolled")
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
        saved = saved.model_copy(update={"luck": luck})
    elif dice is None:
        dice = draw_dice(play.rng, current.dice_count)
    before = state
    state, outcome = resume_outside_damage(
        play.rules_context,
        state,
        command.id,
        current,
        dice,
        secret=pending.secret,
        exposure_command_id=pending.exposure_command.id,
    )
    state = play.checkpoint(state, before=before)
    return (
        state,
        _finish(saved, pending, dice),
        clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            luck=receipt,
            secret=pending.secret,
            outside_event_json=outcome.model_dump_json(),
        ),
    )
