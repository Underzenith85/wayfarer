"""Bind the two selected B464 catalog samples to initialized runtime bodies."""

from typing import TYPE_CHECKING

from wayfarer.engine.rules.conformance import require_capabilities
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.simulation.equipment.basic.vehicles import VEHICLE_INDEX
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


def bind_vehicle(
    engine: ResourceEngine,
    state: ResourceState,
    *,
    definition_id: str,
    transport_id: str,
    body_id: str,
    operator_id: str,
    occupants: tuple[str, ...],
) -> Transport:
    """Pure source-stat projection; caller persists through ordinary setup/CAS.

    This never creates/heals an object or invents occupants, fuel, draft animals,
    cargo, terrain or road facts. Those remain explicit authored scenario facts.
    The complete B464 vehicle table is outside the two-entry sample catalog.
    """
    entry = next((entry for entry in VEHICLE_INDEX if entry.definition_id == definition_id), None)
    if entry is None:
        raise ValidationError("Unknown source vehicle catalog entry")
    require_capabilities("gurps-basic-set-4e-2004", entry.required_capabilities)
    engine.validate(state)
    if not transport_id or any(
        transport.id == transport_id or transport.body_id == body_id
        for transport in state.transports
    ):
        raise ValidationError("Vehicle ID/body is already bound or invalid")
    body = next((item for item in state.items if item.id == body_id), None)
    profile = engine.specs.get(body.definition_id) if body is not None else None
    if (
        body is None
        or body.owner_id != operator_id
        or body.condition is None
        or body.condition.destroyed
        or body.condition.disabled
        or profile is None
        or profile.durability is None
        or profile.durability.construction != "unliving"
        or (profile.durability.hp, profile.durability.dr) != (entry.hp, entry.dr)
    ):
        raise ValidationError("Vehicle requires its source-matching initialized owned body")
    actors = {owner.actor_id for owner in state.owners}
    if (
        operator_id not in occupants
        or not set(occupants) <= actors
        or len(occupants) != len(set(occupants))
        or len(occupants) > entry.occupants
    ):
        raise ValidationError("Vehicle requires known occupants within source capacity")
    return Transport(
        id=transport_id,
        mechanics_version=2,
        locomotion=entry.locomotion,
        body_id=body_id,
        operator_id=operator_id,
        occupants=occupants,
        handling=entry.handling,
        stability=entry.stability,
        acceleration=entry.acceleration,
        top_speed=entry.top_speed,
    )
