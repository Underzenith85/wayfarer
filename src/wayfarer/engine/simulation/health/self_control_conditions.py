"""B428 condition effects on the independent B121 self-control target."""

from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.health.drowsiness import active_sources
from wayfarer.engine.simulation.resources import ResourceState

COWARDICE = "trait:disadvantage:cowardice"


def modifiers(state: ResourceState, actor_id: str, trait_id: str) -> tuple[Modifier, ...]:
    result: tuple[Modifier, ...] = ()
    drunk = next((i for i in state.intoxications if i.actor_id == actor_id), None)
    if drunk is not None and drunk.level in ("tipsy", "drunk") and trait_id != COWARDICE:
        result += (
            Modifier(
                -2 if drunk.level == "tipsy" else -4,
                "Intoxication self-control",
                "intoxication:" + actor_id,
                "Basic Set Campaigns B428",
            ),
        )
    if active_sources(state, actor_id):
        result += (
            Modifier(
                -2, "Drowsiness self-control", "condition:drowsy:" + actor_id, "Basic Set B35/B428"
            ),
        )
    return result
