"""Atomic Luck choice before a cinematic skill's outcome and FP are committed."""

from collections.abc import Mapping

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.skills.cinematic import (
    CinematicSkillCommand,
    CinematicSkillOutcome,
    apply_cinematic_skill,
)
from wayfarer.engine.simulation.skills.physiology import PhysiologyAdjustment
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckState, apply_luck
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError


def apply_lucky_cinematic_skill(
    resources: ResourceState,
    luck: LuckState,
    world: World,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    command: CinematicSkillCommand,
    luck_command: LuckCommand,
    *,
    real_time: int,
    seed: str,
    authorized_actor_id: str,
    system: bool = False,
    physiology: PhysiologyAdjustment | None = None,
) -> tuple[ResourceState, LuckState, CinematicSkillOutcome]:
    """Persist both returned snapshots together; a failure commits neither.

    The cinematic command must still be pending. Already committed checks cannot
    be retroactively changed, including their FP and downstream consequences.
    Exact retry returns the same outcome and original Luck receipt.
    """
    if command.actor_id != luck_command.actor_id or command.id != luck_command.roll_id:
        raise ValidationError("Luck choice does not belong to the cinematic attempt")
    prior_luck = any(use.command == luck_command for use in luck.receipts)
    prior_skill = any(use.command_id == command.id for use in resources.receipts)
    if prior_skill and not prior_luck:
        raise ConflictError("Luck cannot change an already committed cinematic attempt")
    roll = next((value for value in luck.rolls if value.id == command.id), None)
    if roll is None or roll.kind != "success" or roll.scope != "own" or roll.modifier:
        raise ValidationError("Cinematic Luck requires its own unmodified 3d6 roll")
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
    updated_resources, outcome = apply_cinematic_skill(
        resources,
        world,
        build,
        command,
        authorized_actor_id=authorized_actor_id,
        rng=RecordedDice(receipt.attempts[receipt.chosen_index]),
        physiology=physiology,
    )
    return updated_resources, updated_luck, outcome
