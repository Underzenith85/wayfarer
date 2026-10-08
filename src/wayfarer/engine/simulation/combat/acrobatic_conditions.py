"""B428 coughing penalizes DX skills, including the B375 defensive skill check."""

from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.combat.generations import acrobatic_coughing_conditions_enabled
from wayfarer.engine.simulation.health.symptom_state import active
from wayfarer.engine.simulation.resources import ResourceState


def modifiers(state: ResourceState, actor_id: str) -> tuple[Modifier, ...]:
    if not acrobatic_coughing_conditions_enabled():
        return ()
    # Only direct coughing skill penalties are bound here. B421 still exempts
    # temporary attribute reductions; other afflictions need separate consumers.
    result: tuple[Modifier, ...] = ()
    if any(e.spec.kind == "coughing" for e in active(state, actor_id)):
        result += (Modifier(-3, "Symptoms coughing", "B109", "characters-third"),)
    for hazard in state.hazards:
        if hazard.actor_id == actor_id and (
            hazard.affliction_until > state.game_time
            and hazard.spec.affliction == "coughing"
            or hazard.active
            and "coughing" in hazard.conditions
        ):
            result += (Modifier(-3, "Toxin symptoms", "hazard:" + hazard.id, "B428/B439"),)
    for toxin in state.toxins:
        if (
            toxin.actor_id == actor_id
            and toxin.condition_until > state.game_time
            and toxin.profile.condition == "coughing"
        ):
            result += (
                Modifier(
                    -3,
                    "Toxin condition",
                    "toxin:" + toxin.id,
                    "Basic Set Campaigns 4e B428/B438-B441",
                ),
            )
    # Several carriers describe one affliction; B428 supplies one -3 penalty.
    return result[:1]
