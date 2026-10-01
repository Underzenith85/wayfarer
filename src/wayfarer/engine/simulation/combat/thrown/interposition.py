"""B377/B415 sacrificial grenade interception, before blast damage dice."""

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.explosion import BlastResponse
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def intercept(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    responses: tuple[BlastResponse, ...],
    center: GroundPosition,
    contact_actor_id: str | None,
    internal_actor_id: str | None,
) -> tuple[str | None, dict[str, CheckTrace]]:
    attempts = tuple(r for r in responses if r.sacrificial_contact)
    if not attempts:
        return contact_actor_id, {}
    if len(attempts) != 1 or contact_actor_id or internal_actor_id:
        raise ValidationError("One sacrificial contact attempt requires an ordinary explosion")
    response = attempts[0]
    if response.dive_to != center or response.dive_cover_dr:
        raise ValidationError("Sacrificial contact must dive onto the blast center")
    actor = next(p for p in encounter.participants if p.actor_id == response.actor_id)
    if actor.personal_flight is not None and actor.personal_flight.altitude > 0:
        raise ValidationError("Aerial grenade interception requires authored altitude geometry")
    value, _ = defense_value(runtime, state, actor, "dodge")
    assert value is not None
    trace = success_roll("gurps-basic-set-4e-2004", int(value.value) + 3, rng=runtime.rng)
    return (actor.actor_id if trace.outcome.succeeded else None), {actor.actor_id: trace}


def contact_space(encounter: Encounter, response: BlastResponse) -> Encounter:
    """An explicit grenade interception can share the friend's occupied hex."""
    if not response.sacrificial_contact:
        return encounter
    from wayfarer.engine.simulation.combat.thrown.flight import position

    pairs = {
        tuple(sorted((response.actor_id, other.actor_id)))
        for other in encounter.participants
        if other.actor_id != response.actor_id and position(encounter, other) == response.dive_to
    }
    return encounter.model_copy(
        update={"close_pairs": tuple(sorted(set(encounter.close_pairs) | pairs))}
    )
