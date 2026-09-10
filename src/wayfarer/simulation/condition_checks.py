"""Timed condition modifiers, evaluated at the check's authoritative clock.

Keep purchased statistics intact: B361 aftermath changes checks, while B428
retching affects DX, IQ and Per rolls. Neither changes an active defense.
"""

from collections.abc import Mapping

from wayfarer.rules.catalog import RuleDefinition
from wayfarer.rules.checks import Modifier
from wayfarer.simulation.resources import ResourceState


def check_modifiers(
    state: ResourceState, actor_id: str, attribute: str, *, defensive: bool = False
) -> tuple[Modifier, ...]:
    from wayfarer.simulation.fright import aftermath_modifiers, effects

    result = aftermath_modifiers(state, actor_id)
    if (
        not defensive
        and attribute.lower() in ("dx", "iq", "per", "will")
        and any(
            i.actor_id == actor_id and i.active and i.effect.condition == "retching"
            for i in effects(state)
        )
    ):
        result += (Modifier(-5, "Retching", "fright:retching", "Basic Set Campaigns 4e B428"),)
    return result


def definition_modifiers(
    state: ResourceState,
    actor_id: str,
    definition_id: str,
    definitions: Mapping[str, RuleDefinition],
) -> tuple[Modifier, ...]:
    definition = definitions.get(definition_id)
    attribute = (
        str(definition.skill.attribute) if definition and definition.skill else definition_id
    ).split(":")[-1]
    return check_modifiers(state, actor_id, attribute)


def retching_penalty(state: ResourceState, actor_id: str) -> int:
    from wayfarer.simulation.fright import effects

    return (
        -5
        if any(
            i.actor_id == actor_id and i.active and i.effect.condition == "retching"
            for i in effects(state)
        )
        else 0
    )
