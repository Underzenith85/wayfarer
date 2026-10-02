"""One admission and selection policy for every private reaction continuation."""

from dataclasses import dataclass

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.social.gurps_social import (
    ReactionModifier,
    ReactionTrace,
    evaluate_reaction,
    reaction_roll,
)
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.traits.luck import (
    LuckCommand,
    LuckReceipt,
    LuckState,
    apply_luck,
    luck_cooldown,
    validate_luck_use,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_records import ChooseReaction, SecretReactionPending
from wayfarer.orchestration.real_play_clock import RealPlayClock, spend_real_play_cooldown
from wayfarer.orchestration.task_context import approved
from wayfarer.orchestration.task_records import TaskSnapshot


@dataclass(frozen=True)
class ReactionAdmission:
    build: ValidatedBuild | None
    luck: LuckState
    request: LuckCommand
    clock: RealPlayClock


def admit_reaction(
    play: PlayService,
    state: PlayState,
    command: ChooseReaction,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> ReactionAdmission:
    luck = saved.luck.model_copy(
        update={"revision": state.revision, "game_time": state.resources.game_time}
    )
    request = LuckCommand(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=state.revision,
        roll_id=command.pending_id,
    )
    build = None
    if command.choice == "use-luck":
        build = approved(play, state, command.actor_id)
        definitions = play.engine.reviewer.compiler.definitions
        clock = spend_real_play_cooldown(
            clock, actor_id=command.actor_id, seconds=luck_cooldown(build, definitions)
        )
        validate_luck_use(
            luck,
            request,
            build,
            definitions,
            real_time=clock.elapsed_seconds,
            authorized_actor_id=command.actor_id,
            system=True,
        )
    return ReactionAdmission(build, luck, request, clock)


def select_reaction(
    play: PlayService,
    admitted: ReactionAdmission,
    profile_id: str,
    modifiers: tuple[ReactionModifier, ...],
) -> tuple[LuckState, LuckReceipt | None, ReactionTrace]:
    luck, request, build, clock = admitted.luck, admitted.request, admitted.build, admitted.clock
    receipt = None
    if build is None:
        selected = reaction_roll(profile_id, modifiers, rng=play.rng)
    else:
        luck = luck.model_copy(
            update={
                "rolls": tuple(
                    roll.model_copy(
                        update={"modifier": sum(modifier.value for modifier in modifiers)}
                    )
                    if roll.id == request.roll_id
                    else roll
                    for roll in luck.rolls
                )
            }
        )
        luck, receipt = apply_luck(
            luck,
            request,
            build,
            play.engine.reviewer.compiler.definitions,
            real_time=clock.elapsed_seconds,
            rng=play.rng,
            authorized_actor_id=request.actor_id,
            system=True,
        )
        dice = receipt.attempts[receipt.chosen_index]
        selected = evaluate_reaction(profile_id, modifiers, (dice[0], dice[1], dice[2]))
    return luck, receipt, selected


def finish_reaction(
    saved: TaskSnapshot, pending: SecretReactionPending, selected: ReactionTrace | None
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
                                "chosen_dice": selected.dice,
                                "chosen_total": selected.total,
                                "modifier": selected.total - sum(selected.dice),
                            }
                        )
                        if roll.id == pending.id and selected is not None
                        else roll
                        for roll in saved.luck.rolls
                    ),
                }
            ),
        }
    )
