"""B66 Luck commits an ordinary task's selected roll and actual progress together."""

from collections.abc import Mapping

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.activities import (
    ActivityActor,
    ActivityOutcome,
    AdvanceClock,
    LongTaskRule,
    LoseFatigue,
    PerformActivity,
    apply_activity,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckState, apply_luck
from wayfarer.errors import ConflictError, ValidationError


def apply_lucky_task(
    resources: ResourceState,
    luck: LuckState,
    command: PerformActivity,
    luck_command: LuckCommand,
    rule: LongTaskRule,
    actor: ActivityActor,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    *,
    advance: AdvanceClock,
    lose_fatigue: LoseFatigue,
    real_time: int,
    seed: str,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, LuckState, ActivityOutcome]:
    """The host persists both snapshots under one lock; exceptions commit neither.

    The selected roll is the worker's own check. Supervision is a separate actor's
    check and cannot consume or borrow this actor's Luck.
    """
    if command.actor_id != luck_command.actor_id or command.id != luck_command.roll_id:
        raise ValidationError("Luck is not bound to this task attempt")
    if command.supervisor_target is not None:
        raise ValidationError("Supervised task Luck requires separately bound pending checks")
    roll = next((value for value in luck.rolls if value.id == command.id), None)
    if roll is None or roll.kind != "success" or roll.scope != "own" or roll.modifier:
        raise ValidationError("Task Luck requires the worker's own unmodified pending 3d6")
    prior_task = any(value.command_id == command.id for value in resources.receipts)
    prior_luck = any(value.command == luck_command for value in luck.receipts)
    if prior_task and not prior_luck:
        raise ConflictError("Luck cannot change an already committed task")
    updated_luck, receipt = apply_luck(
        luck,
        luck_command,
        build,
        definitions,
        real_time=real_time,
        seed=seed,
        authorized_actor_id=authorized_actor_id,
        system=system,
    )
    updated_resources, outcome = apply_activity(
        resources,
        command,
        rule,
        actor,
        rng=RecordedDice(receipt.attempts[receipt.chosen_index]),
        advance=advance,
        lose_fatigue=lose_fatigue,
        system=system,
    )
    return updated_resources, updated_luck, outcome
