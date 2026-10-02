"""One source-bound success-roll Luck policy for opponent attacks and fragments."""

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.traits.luck import (
    LuckCommand,
    LuckReceipt,
    apply_luck,
    luck_cooldown,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.opponent_attack_records import (
    ChooseOpponentAttack,
    OpponentAttackPending,
)
from wayfarer.orchestration.opponent_fragment_records import (
    ChooseOpponentFragment,
    OpponentFragmentPending,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock, spend_real_play_cooldown
from wayfarer.orchestration.task_context import approved
from wayfarer.orchestration.task_records import TaskSnapshot


def _score(
    pending: OpponentAttackPending | OpponentFragmentPending, dice: tuple[int, ...]
) -> CheckTrace:
    if len(dice) != 3:
        raise ValidationError("Opponent attacks require exactly three dice")
    return pending.preparation.spec.score((dice[0], dice[1], dice[2]))


def select_opponent_roll(
    play: PlayService,
    state: PlayState,
    command: ChooseOpponentAttack | ChooseOpponentFragment,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    pending: OpponentAttackPending | OpponentFragmentPending,
) -> tuple[TaskSnapshot, RealPlayClock, CheckTrace, CheckTrace, LuckReceipt | None]:
    luck = saved.luck.model_copy(
        update={"revision": state.revision, "game_time": state.resources.game_time}
    )
    original = pending.original
    receipt = None
    if command.choice == "use-luck":
        owner = approved(play, state, command.actor_id)
        if any(
            p.trait is not None and p.trait.modifiers
            for p in owner.trait_purchases
            if p.definition_id == "trait:advantage:luck"
        ):
            raise ValidationError("Variant Luck requires its separate source-bound consumer")
        previous = next((c for c in clock.cooldowns if c.actor_id == command.actor_id), None)
        if (
            not pending.secret
            and previous is not None
            and pending.opened_elapsed_microseconds < previous.available_at_microseconds
        ):
            raise ValidationError("Luck was cooling down when this original was rolled")
        definitions = play.engine.reviewer.compiler.definitions
        clock = spend_real_play_cooldown(
            clock, actor_id=command.actor_id, seconds=luck_cooldown(owner, definitions)
        )
        luck, receipt = apply_luck(
            luck,
            LuckCommand(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                roll_id=pending.id,
            ),
            owner,
            definitions,
            real_time=clock.elapsed_seconds,
            rng=play.rng,
            authorized_actor_id=command.actor_id,
            system=True,
        )
        selected = _score(pending, receipt.attempts[receipt.chosen_index])
        original = original or _score(pending, receipt.attempts[0])
    else:
        original = original or pending.preparation.spec.roll(play.rng)
        selected = original
    luck = luck.model_copy(
        update={
            "pending_roll_id": None,
            "rolls": tuple(
                r.model_copy(update={"chosen_dice": selected.dice, "chosen_total": selected.total})
                if r.id == pending.id
                else r
                for r in luck.rolls
            ),
        }
    )
    return (
        saved.model_copy(update={"pending": None, "luck": luck}),
        clock,
        original,
        selected,
        receipt,
    )
