"""Timed condition modifiers, evaluated at the check's authoritative clock.

Keep purchased statistics intact: B361 aftermath changes checks, while B428
retching affects DX, IQ and Per rolls. Neither changes an active defense.
"""

from collections.abc import Mapping

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def check_modifiers(
    state: ResourceState, actor_id: str, attribute: str, *, defensive: bool = False
) -> tuple[Modifier, ...]:
    from wayfarer.engine.simulation.health.fright import aftermath_modifiers, effects

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
    for hazard in state.hazards:
        if hazard.actor_id != actor_id:
            continue
        penalty = 0
        if hazard.affliction_until > state.game_time and hazard.spec.affliction == "coughing":
            penalty = -3 if attribute.lower() == "dx" else -1 if attribute.lower() == "iq" else 0
        if hazard.active and hazard.spec.variant == "cobra-venom" and attribute.lower() == "dx":
            fraction = hazard.symptoms * 6 // hazard.full_hp
            penalty += -6 if fraction >= 4 else -4 if fraction >= 3 else -2 if fraction >= 2 else 0
        if penalty:
            result += (Modifier(penalty, "Toxin symptoms", "hazard:" + hazard.id, "B428/B439"),)
    return result


def definition_modifiers(
    state: ResourceState,
    actor_id: str,
    definition_id: str,
    definitions: Mapping[str, RuleDefinition],
) -> tuple[Modifier, ...]:
    if definition_id == "skill:stealth":
        require_hazard_capacity(state, actor_id, "stealth")
    definition = definitions.get(definition_id)
    attribute = (
        str(definition.skill.attribute) if definition and definition.skill else definition_id
    ).split(":")[-1]
    return check_modifiers(state, actor_id, attribute)


def retching_penalty(state: ResourceState, actor_id: str) -> int:
    from wayfarer.engine.simulation.health.fright import effects

    return (
        -5
        if any(
            i.actor_id == actor_id and i.active and i.effect.condition == "retching"
            for i in effects(state)
        )
        else 0
    )


def require_hazard_capacity(state: ResourceState, actor_id: str, kind: str) -> None:

    for hazard in state.hazards:
        if hazard.actor_id != actor_id or hazard.affliction_until <= state.game_time:
            continue
        if hazard.spec.affliction == "paralysis" and kind not in ("question", "wait"):
            raise ValidationError("Paralysis prevents voluntary physical action")
        if hazard.spec.affliction == "coughing" and kind == "stealth":
            raise ValidationError("Coughing prevents Stealth")
        if hazard.spec.affliction == "blindness" and kind == "vision":
            raise ValidationError("Toxin blindness prevents vision")
