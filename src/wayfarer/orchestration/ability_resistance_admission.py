"""Current B348 score admission before native injury, fatigue and distraction."""

from wayfarer.engine.rules.supernatural.ability_types import AbilitySpec
from wayfarer.engine.simulation.abilities import AbilityContext, admit_ability_resistance
from wayfarer.engine.simulation.ability_resistance_origin import current, record
from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def admit(
    state: PlayState, command: AbilityCommand, spec: AbilitySpec, context: AbilityContext
) -> AbilityContext:
    if context.current_resistance and spec.kind not in {"burning-malediction", "mind-reading"}:
        raise ValidationError("Current resistance policy requires a resisted ability")
    if (
        command.kind == "resolve"
        and spec.kind in {"burning-malediction", "mind-reading"}
        and context.current_resistance != current(state.resources, command)
    ):
        raise ValidationError("Resistance resolve changed its initiating producer policy")
    return admit_ability_resistance(state.resources, command, spec, context)


def record_origin(
    resources: ResourceState, command: AbilityCommand, *, enabled: bool
) -> ResourceState:
    return record(resources, command) if enabled and command.kind == "activate" else resources
