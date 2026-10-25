"""B361 direct skill penalties survive the B421 defensive-attribute exemption."""

from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.combat.generations import acrobatic_condition_checks_enabled
from wayfarer.engine.simulation.health.fright_state import aftermath_modifiers
from wayfarer.engine.simulation.resources import ResourceState


def condition_modifiers(state: ResourceState, actor_id: str) -> tuple[Modifier, ...]:
    """Read currently active skill penalties without reapplying attribute loss.

    B361 rows 28–30 penalize all skill rolls after recovery. B428 coughing,
    intoxication and retching instead lower attributes, which B421 exempts for
    this maneuver-free reaction. Other B428 direct skill conditions require
    their own supported producers; their names alone grant no runtime effect.
    """
    if not acrobatic_condition_checks_enabled():
        return ()
    return aftermath_modifiers(state, actor_id)
