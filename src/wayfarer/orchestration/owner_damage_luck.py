"""One clock and selection policy for every typed owner damage continuation."""

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.traits.luck import (
    LuckCommand,
    LuckReceipt,
    apply_luck,
    luck_cooldown,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamagePending
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock, spend_real_play_cooldown
from wayfarer.orchestration.task_context import approved
from wayfarer.orchestration.task_records import TaskSnapshot


def _ordinary_luck(play: PlayService, state: PlayState, actor_id: str) -> int:
    compiled = approved(play, state, actor_id)
    cooldown = luck_cooldown(compiled, play.engine.reviewer.compiler.definitions)
    purchase = next(
        p for p in compiled.trait_purchases if p.definition_id == "trait:advantage:luck"
    )
    if purchase.trait is None or purchase.trait.modifiers:
        raise ValidationError("This damage consumer requires ordinary unmodified Luck")
    return cooldown


def select_owner_damage(
    play: PlayService,
    state: PlayState,
    command: ChooseOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    pending: OwnerDamagePending | InventoryDamagePending,
) -> tuple[TaskSnapshot, RealPlayClock, LuckReceipt | None, tuple[int, ...]]:
    luck = saved.luck.model_copy(
        update={"revision": state.revision, "game_time": state.resources.game_time}
    )
    receipt = None
    if command.choice == "use-luck":
        seconds = _ordinary_luck(play, state, command.actor_id)
        prior = next(
            (entry for entry in clock.cooldowns if entry.actor_id == command.actor_id), None
        )
        if (
            not pending.secret
            and prior is not None
            and pending.opened_elapsed_microseconds < prior.available_at_microseconds
        ):
            raise ValidationError("Luck was cooling down when this original was rolled")
        clock = spend_real_play_cooldown(clock, actor_id=command.actor_id, seconds=seconds)
        luck, receipt = apply_luck(
            luck,
            LuckCommand(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                roll_id=pending.id,
            ),
            approved(play, state, command.actor_id),
            play.engine.reviewer.compiler.definitions,
            real_time=clock.elapsed_seconds,
            rng=play.rng,
            authorized_actor_id=command.actor_id,
            system=True,
        )
        selected = receipt.attempts[receipt.chosen_index]
    else:
        selected = (
            pending.original
            if pending.original is not None
            else draw_dice(play.rng, pending.preparation.dice_count)
        )
    luck = luck.model_copy(
        update={
            "pending_roll_id": None,
            "rolls": tuple(
                roll.model_copy(
                    update={"chosen_dice": selected, "chosen_total": sum(selected) + roll.modifier}
                )
                if roll.id == pending.id
                else roll
                for roll in luck.rolls
            ),
        }
    )
    saved = saved.model_copy(update={"pending": None, "luck": luck})
    return saved, clock, receipt, selected
