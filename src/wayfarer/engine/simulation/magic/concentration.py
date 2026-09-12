"""One concentration commitment per actor across supernatural services."""

from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


def require_idle_concentration(resources: ResourceState, actor_id: str) -> None:
    """Reject overlapping actions before injury rolls, fatigue or new receipts.

    Active maintained effects are not pending concentration. A pending cast
    stays a commitment until explicitly resolved, interrupted or cancelled,
    including after restart or a missed deadline.
    """
    from wayfarer.engine.simulation.health.condition_checks import retching_penalty

    if retching_penalty(resources, actor_id):
        raise ConflictError("Retching prevents concentration")
    from wayfarer.engine.simulation.abilities import effects
    from wayfarer.engine.simulation.magic.spells import latest

    if any(e.actor_id == actor_id and e.concentrating for e in effects(resources)) or any(
        e.actor_id == actor_id and e.phase == "casting" for e in latest(resources).values()
    ):
        raise ConflictError("Actor is already concentrating")
