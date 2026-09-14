"""Authoritative equipment binding for B116-117 gadget limitations.

Custody, damage, repair, and reacquisition remain owned by the resource and
object reducers.  This adapter projects those persisted facts into ability
availability without creating a parallel gadget state.
"""

from typing import Final, Literal

from pydantic import Field

from wayfarer.engine.rules.traits.modifiers import (
    BREAKABLE,
    STOLEN,
    UNIQUE,
    GadgetConstruction,
    GadgetState,
    ModifierSelection,
    gadget_available,
)
from wayfarer.engine.simulation.resources import EquipmentSpec, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record

PROFILE: Final[Literal["gurps-basic-set-4e-2004"]] = "gurps-basic-set-4e-2004"


class GadgetAbilityBinding(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    ability_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    definition_id: str = Field(min_length=1, max_length=200)


class GadgetAvailabilityReceipt(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    revision: int
    ability_id: str
    actor_id: str
    item_id: str
    available: bool
    reason: Literal["available", "not-held", "broken", "stolen", "lost-unique"]


def _construction(selections: tuple[ModifierSelection, ...]) -> GadgetConstruction:
    selected = {selection.definition_id: selection for selection in selections}
    gadget_rows = set(selected) & {BREAKABLE, STOLEN, UNIQUE}
    if not gadget_rows:
        raise ValidationError("Ability has no gadget limitation")
    facts = tuple(
        selection.gadget
        for selection in selections
        if selection.definition_id in {BREAKABLE, STOLEN} and selection.gadget is not None
    )
    if not facts or any(fact != facts[0] for fact in facts[1:]):
        raise ValidationError("Gadget limitations require one consistent construction")
    return facts[0]


def resolve_gadget_availability(
    state: ResourceState,
    binding: GadgetAbilityBinding,
    selections: tuple[ModifierSelection, ...],
    specs: dict[str, EquipmentSpec],
) -> GadgetAvailabilityReceipt:
    """Read one exact persisted item at one CAS revision and return a replayable receipt."""
    facts = _construction(selections)
    selected = {selection.definition_id for selection in selections}
    item = next((candidate for candidate in state.items if candidate.id == binding.item_id), None)
    unique = UNIQUE in selected
    if item is None:
        return GadgetAvailabilityReceipt(
            revision=state.revision,
            ability_id=binding.ability_id,
            actor_id=binding.actor_id,
            item_id=binding.item_id,
            available=False,
            reason="lost-unique" if unique else "not-held",
        )
    if item.definition_id != binding.definition_id:
        raise ValidationError("Bound gadget definition changed")
    broken = False
    if BREAKABLE in selected:
        spec = specs.get(item.definition_id)
        if spec is None or spec.durability is None or item.condition is None:
            raise ValidationError("Breakable gadget requires authoritative durability state")
        durability = spec.durability
        if durability.profile_id != PROFILE:
            raise ValidationError("Gadget durability uses the wrong rules profile")
        if (
            durability.dr != facts.damage_resistance
            or durability.size_modifier != facts.size_modifier
        ):
            raise ValidationError("Gadget construction disagrees with authoritative item facts")
        if not facts.repairable and (
            durability.repair_skill_id is not None
            or durability.repair_tools_definition is not None
            or durability.repair_parts_definition is not None
        ):
            raise ValidationError("Irreparable gadget exposes an authoritative repair route")
        broken = item.condition.disabled or item.condition.destroyed
    stolen = item.owner_id != binding.actor_id
    held = not stolen and item.ground is None
    projected = GadgetState(held=held, broken=broken, stolen=stolen)
    available = gadget_available(selections, projected)
    reason: Literal["available", "not-held", "broken", "stolen", "lost-unique"]
    if available:
        reason = "available"
    elif broken:
        reason = (
            "lost-unique" if unique and item.condition and item.condition.destroyed else "broken"
        )
    elif stolen:
        reason = "stolen"
    else:
        reason = "not-held"
    return GadgetAvailabilityReceipt(
        revision=state.revision,
        ability_id=binding.ability_id,
        actor_id=binding.actor_id,
        item_id=item.id,
        available=available,
        reason=reason,
    )
