"""Resume one real exposure after its fixed prerequisite and selected damage."""

from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hazard_damage import (
    HazardDamageSelection,
    PreparedHazardDamage,
)
from wayfarer.engine.simulation.health.hazard_visibility import (
    SecretHazardResult,
    conceal_hazard_result,
)
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.orchestration.outside_event_prerequisites import require_settled_prerequisite
from wayfarer.orchestration.outside_event_records import OutsideEventOutcome


def resume_outside_damage(
    runtime: RulesContext,
    state: PlayState,
    command_id: str,
    preparation: PreparedHazardDamage,
    dice: tuple[int, ...],
    *,
    secret: bool,
    exposure_command_id: str,
) -> tuple[PlayState, OutsideEventOutcome]:
    require_settled_prerequisite(state, preparation)
    selected = HazardDamageSelection(preparation=preparation, dice=dice)
    actor_id = preparation.schedule.actor_id
    resources, consequence = apply_hazard(
        state.resources,
        HazardCommand(
            id=command_id,
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            kind="resolve",
            hazard_id=preparation.schedule.spec.id,
        ),
        preparation.schedule,
        rng=runtime.rng,
        system=True,
        selected_damage=selected,
        physiology=physiology_traits(
            runtime.approved_build(state, actor_id), runtime.reviewer.compiler.definitions
        ),
    )
    if secret:
        resources = conceal_hazard_result(
            resources,
            SecretHazardResult(
                command_id=command_id,
                actor_id=actor_id,
                schedule_id=preparation.schedule.id,
                exposure_command_id=exposure_command_id,
            ),
            system=True,
        )
    return state.model_copy(update={"resources": resources}), OutsideEventOutcome(
        schedule_id=preparation.schedule.id,
        dice=dice,
        basic_damage=selected.basic_damage,
        consequence=consequence,
    )
