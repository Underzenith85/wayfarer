"""Timed condition modifiers, evaluated at the check's authoritative clock.

Keep purchased statistics intact: B361 aftermath changes checks, while B428
retching affects DX, IQ and Per rolls. Neither changes an active defense.
"""

from collections.abc import Mapping

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.health.condition_generations import (
    current_attributes,
    deduplicate_drowsiness,
)
from wayfarer.engine.simulation.health.drowsiness import active_sources as drowsy_sources
from wayfarer.engine.simulation.health.drowsiness import current as current_drowsiness
from wayfarer.engine.simulation.health.drug_state import drug_unconscious
from wayfarer.engine.simulation.health.fright_state import aftermath_modifiers, effects
from wayfarer.engine.simulation.health.symptom_state import active as active_symptoms
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.magic.awaken_state import alert_until
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def _attribute_penalty(attribute: str, *, dx: int, iq: int, defensive: bool, current: bool) -> int:
    """B421 propagates IQ loss and exempts maneuver-free defensive reactions."""
    if current and defensive:
        return 0
    if attribute.lower() == "dx":
        return dx
    if attribute.lower() == "iq" or current and attribute.lower() in ("per", "will"):
        return iq
    return 0


def _survival_drowsiness(
    state: ResourceState, actor_id: str, attribute: str, defensive: bool, current: bool
) -> tuple[Modifier, ...]:
    survival = next((entry for entry in state.survival if entry.actor_id == actor_id), None)
    if (
        survival is None
        or survival.drowsy_until is None
        or survival.drowsy_until <= state.game_time
        or alert_until(state, actor_id)
    ):
        return ()
    penalty = _attribute_penalty(attribute, dx=-2, iq=-2, defensive=defensive, current=current)
    return (
        (Modifier(penalty, "Drowsiness", "survival:missed-sleep", "Basic Set Campaigns 4e B427"),)
        if penalty
        else ()
    )


def _single_drowsiness(
    state: ResourceState, actor_id: str, attribute: str, defensive: bool
) -> tuple[Modifier, ...]:
    penalty = _attribute_penalty(attribute, dx=-2, iq=-2, defensive=defensive, current=True)
    if penalty and drowsy_sources(state, actor_id):
        return (
            Modifier(
                penalty, "Drowsiness", "condition:drowsy:" + actor_id, "Basic Set B35/B421/B428"
            ),
        )
    return ()


def check_modifiers(
    state: ResourceState, actor_id: str, attribute: str, *, defensive: bool = False
) -> tuple[Modifier, ...]:
    result = aftermath_modifiers(state, actor_id)
    current = current_attributes()
    deduplicate = deduplicate_drowsiness()
    intoxication = next((i for i in state.intoxications if i.actor_id == actor_id), None)
    if intoxication and intoxication.level != "sober":
        # B428/B440: waking from a stupor does not sober the subject.
        amount = -1 if intoxication.level == "tipsy" else -2
        penalty = _attribute_penalty(
            attribute, dx=amount, iq=amount, defensive=defensive, current=current
        )
        if penalty:
            result += (
                Modifier(
                    penalty,
                    "Intoxication",
                    "intoxication:" + actor_id,
                    "Basic Set Campaigns 4e B428/B440",
                ),
            )
    result += (
        _single_drowsiness(state, actor_id, attribute, defensive)
        if deduplicate
        else _survival_drowsiness(state, actor_id, attribute, defensive, current)
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
    if not defensive and any(e.spec.kind == "coughing" for e in active_symptoms(state, actor_id)):
        penalty = _attribute_penalty(attribute, dx=-3, iq=-1, defensive=defensive, current=current)
        if penalty:
            result += (Modifier(penalty, "Symptoms coughing", "B109", "characters-third"),)
    for hazard in state.hazards:
        if hazard.actor_id != actor_id:
            continue
        penalty = 0
        if (hazard.affliction_until > state.game_time and hazard.spec.affliction == "coughing") or (
            hazard.active and "coughing" in hazard.conditions
        ):
            penalty = _attribute_penalty(
                attribute, dx=-3, iq=-1, defensive=defensive, current=current
            )
        if hazard.active and hazard.spec.variant == "cobra-venom":
            fraction = hazard.symptoms * 6 // hazard.full_hp
            amount = -6 if fraction >= 4 else -4 if fraction >= 3 else -2 if fraction >= 2 else 0
            penalty += _attribute_penalty(
                attribute, dx=amount, iq=0, defensive=defensive, current=current
            )
        if penalty:
            result += (Modifier(penalty, "Toxin symptoms", "hazard:" + hazard.id, "B428/B439"),)
    drowsy = next(
        (
            toxin
            for toxin in state.toxins
            if toxin.actor_id == actor_id
            and toxin.condition_until > state.game_time
            and toxin.profile.condition == "drowsy"
            and (current_drowsiness(state, toxin.id, toxin.actor_id) or current)
        ),
        None,
    )
    if (
        not deduplicate
        and drowsy is not None
        and not defensive
        and attribute.lower() in ("dx", "iq", "per", "will")
    ):
        result += (
            Modifier(
                -2,
                "Toxin drowsiness",
                "toxin:" + drowsy.id,
                "Basic Set Campaigns 4e B428",
            ),
        )
    for toxin in state.toxins:
        if toxin.actor_id != actor_id or toxin.condition_until <= state.game_time:
            continue
        condition = toxin.profile.condition
        if condition == "drowsy" and (deduplicate or drowsy is not None):
            continue
        dx, iq = (
            (-3, -1) if condition == "coughing" else (0, -1) if condition == "drowsy" else (0, 0)
        )
        penalty = _attribute_penalty(attribute, dx=dx, iq=iq, defensive=defensive, current=current)
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
    if kind == "vision" and acute_blindness(state, actor_id):
        raise ValidationError("Symptoms blindness prevents vision")
    for effect in active_symptoms(state, actor_id):
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
