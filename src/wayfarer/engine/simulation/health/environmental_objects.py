"""Environmental fire damage composed with the authoritative object reducer."""

from __future__ import annotations

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.environmental_hazards import CombustionFacts, ignition_threshold
from wayfarer.engine.rules.types.object import ObjectResult
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class CombustionResult(Record):
    damage: ObjectResult
    ignited: bool


def apply_burning_object(
    engine: ResourceEngine,
    state: ResourceState,
    command: DamageObject,
    facts: CombustionFacts,
    *,
    rng: RandomSource,
) -> tuple[ResourceState, CombustionResult]:
    """Apply damage once, then derive ignition from that same authored roll."""
    if command.damage_type != "burn":
        raise ValidationError("Combustion requires burning object damage")
    updated, result = apply_object(engine, state, command, system=True, rng=rng)
    threshold = ignition_threshold(facts.material, tight_beam=facts.tight_beam)
    return updated, CombustionResult(
        damage=result,
        ignited=threshold is not None and command.basic_damage >= threshold,
    )
