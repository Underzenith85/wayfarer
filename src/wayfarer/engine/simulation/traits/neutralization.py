"""Durable Neutralize facts read by later psionic consumers."""

from pydantic import Field

from wayfarer.engine.rules.traits.psi_powers import BINDING_BY_ID
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record

PREFIX = "neutralization:"
COOLDOWN_PREFIX = "neutralize-crippled:"
PSI_FAMILIES = frozenset(
    {
        "power:antipsi",
        "power:esp",
        "power:psychic-healing",
        "power:psychokinesis",
        "power:telepathy",
        "power:teleportation",
    }
)


class Neutralization(Record):
    actor_id: str
    target_id: str
    power_id: str | None = None
    effect_id: str
    expires_at: int = Field(ge=1)


class NeutralizeCooldown(Record):
    actor_id: str
    expires_at: int = Field(ge=1)


def suppressions(resources: ResourceState) -> tuple[Neutralization, ...]:
    return tuple(
        Neutralization.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def power_suppressed(
    resources: ResourceState, actor_id: str, power_id: str, ability_id: str | None = None
) -> bool:
    if power_id != "psi" and power_id not in PSI_FAMILIES:
        return False
    return any(
        effect.target_id == actor_id
        and effect.effect_id in resources.active_effect_ids
        and resources.game_time < effect.expires_at
        and (
            effect.power_id is None
            or effect.power_id == power_id
            or (
                power_id == "psi"
                and ability_id is not None
                and ability_id in BINDING_BY_ID[effect.power_id].members
            )
        )
        for effect in suppressions(resources)
    )


def neutralize_crippled(resources: ResourceState, actor_id: str) -> bool:
    return any(
        NeutralizeCooldown.model_validate_json(event.kind).actor_id == actor_id
        and resources.game_time < NeutralizeCooldown.model_validate_json(event.kind).expires_at
        for event in resources.events
        if event.id.startswith(COOLDOWN_PREFIX)
    )
