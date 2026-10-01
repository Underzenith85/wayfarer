"""Timed condition modifiers, evaluated at the check's authoritative clock.

Keep purchased statistics intact: B361 aftermath changes checks, while B428
retching affects DX, IQ and Per rolls. Neither changes an active defense.
"""

from collections.abc import Mapping

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.health.drug_state import drug_unconscious
from wayfarer.engine.simulation.health.fright_state import aftermath_modifiers, effects
from wayfarer.engine.simulation.health.symptom_state import active as active_symptoms
from wayfarer.engine.simulation.magic.awaken_state import alert_until
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def check_modifiers(
    state: ResourceState, actor_id: str, attribute: str, *, defensive: bool = False
) -> tuple[Modifier, ...]:
    result = aftermath_modifiers(state, actor_id)
    intoxication = next((i for i in state.intoxications if i.actor_id == actor_id), None)
    if intoxication and intoxication.level != "sober" and attribute.lower() in ("dx", "iq"):
        # B428/B440: waking from a stupor does not sober the subject.
        result += (
            Modifier(
                -1 if intoxication.level == "tipsy" else -2,
                "Intoxication",
                "intoxication:" + actor_id,
                "Basic Set Campaigns 4e B428/B440",
            ),
        )
    survival = next((entry for entry in state.survival if entry.actor_id == actor_id), None)
    if (
        survival is not None
        and survival.drowsy_until is not None
        and survival.drowsy_until > state.game_time
        and attribute.lower() in ("dx", "iq")
        and not alert_until(state, actor_id)
    ):
        result += (
            Modifier(-2, "Drowsiness", "survival:missed-sleep", "Basic Set Campaigns 4e B427"),
        )
    if (
        not defensive
        and attribute.lower() in ("dx", "iq", "per", "will")
        and any(
            i.actor_id == actor_id and i.active and i.effect.condition == "retching"
            for i in effects(state)
        )
    ):
        result += (Modifier(-5, "Retching", "fright:retching", "Basic Set Campaigns 4e B428"),)
    if (
        not defensive
        and attribute.lower() in {"dx", "iq"}
        and any(e.spec.kind == "coughing" for e in active_symptoms(state, actor_id))
    ):
        result += (
            Modifier(
                -3 if attribute.lower() == "dx" else -1,
                "Symptoms coughing",
                "B109",
                "characters-third",
            ),
        )
    for hazard in state.hazards:
        if hazard.actor_id != actor_id:
            continue
        penalty = 0
        if (hazard.affliction_until > state.game_time and hazard.spec.affliction == "coughing") or (
            hazard.active and "coughing" in hazard.conditions
        ):
            penalty = -3 if attribute.lower() == "dx" else -1 if attribute.lower() == "iq" else 0
        if hazard.active and hazard.spec.variant == "cobra-venom" and attribute.lower() == "dx":
            fraction = hazard.symptoms * 6 // hazard.full_hp
            penalty += -6 if fraction >= 4 else -4 if fraction >= 3 else -2 if fraction >= 2 else 0
        if penalty:
            result += (Modifier(penalty, "Toxin symptoms", "hazard:" + hazard.id, "B428/B439"),)
    for toxin in state.toxins:
        if toxin.actor_id != actor_id or toxin.condition_until <= state.game_time:
            continue
        condition = toxin.profile.condition
        penalty = (
            -3
            if condition == "coughing" and attribute.lower() == "dx"
            else -1
            if condition in ("coughing", "drowsy") and attribute.lower() == "iq"
            else 0
        )
        if penalty:
            result += (
                Modifier(
                    penalty,
                    "Toxin condition",
                    "toxin:" + toxin.id,
                    "Basic Set Campaigns 4e B428/B438-B441",
                ),
            )
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
    return (
        -5
        if any(
            i.actor_id == actor_id and i.active and i.effect.condition == "retching"
            for i in effects(state)
        )
        else 0
    )


def require_hazard_capacity(state: ResourceState, actor_id: str, kind: str) -> None:
    if kind not in ("question", "wait") and drug_unconscious(state, actor_id):
        raise ValidationError("An unconscious drugged actor cannot act")
    for effect in active_symptoms(state, actor_id):
        if effect.spec.kind == "blindness" and kind == "vision":
            raise ValidationError("Symptoms blindness prevents vision")
        if effect.spec.kind == "coughing" and kind == "stealth":
            raise ValidationError("Symptoms coughing prevents Stealth")

    for hazard in state.hazards:
        if hazard.actor_id != actor_id or hazard.affliction_until <= state.game_time:
            continue
        if hazard.spec.affliction == "paralysis" and kind not in ("question", "wait"):
            raise ValidationError("Paralysis prevents voluntary physical action")
        if hazard.spec.affliction == "coughing" and kind == "stealth":
            raise ValidationError("Coughing prevents Stealth")
        if hazard.spec.affliction == "blindness" and kind == "vision":
            raise ValidationError("Toxin blindness prevents vision")
        if "blindness" in hazard.conditions and kind == "vision":
            raise ValidationError("Atmospheric injury prevents vision")
    for toxin in state.toxins:
        if toxin.actor_id != actor_id or toxin.condition_until <= state.game_time:
            continue
        if toxin.profile.condition == "paralysis" and kind not in ("question", "wait"):
            raise ValidationError("Paralysis prevents voluntary physical action")
        if toxin.profile.condition == "coughing" and kind == "stealth":
            raise ValidationError("Coughing prevents Stealth")
        if toxin.profile.condition == "blindness" and kind == "vision":
            raise ValidationError("Toxin blindness prevents vision")
