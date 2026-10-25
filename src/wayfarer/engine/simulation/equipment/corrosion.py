"""Current authenticated admission for the bounded B379 physical armor lane."""

from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.equipment.corrosion_state import (
    CorrosionProtection,
    erosion,
    profile_digest,
    projected_profile,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def protection(runtime: RulesContext, state: PlayState, actor_id: str) -> CorrosionProtection:
    compiled = build(runtime, state, actor_id, defensive=True)
    traits = attack_defense_traits(compiled, runtime.reviewer.compiler.definitions)
    if traits.damage_resistance() or damage_resistance(
        state.resources, actor_id, build_revision=compiled.revision
    ):
        raise ValidationError("Corrosion natural and active DR compositions are unsupported")
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    worn = tuple(
        (item, entries[item.definition_id])
        for item in state.resources.items
        if item.owner_id == actor_id
        and item.equipped
        and (item.condition is None or not item.condition.disabled)
        and entries[item.definition_id].armor is not None
    )
    if not worn:
        return CorrosionProtection(actor_id=actor_id)
    if len(worn) != 1:
        raise ValidationError("Corrosion requires one unambiguous physical torso armor item")
    item, entry = worn[0]
    assert entry.armor is not None
    armor = entry.armor
    if (
        item.quantity != 1
        or item.enchantments
        or item.condition is not None
        and item.condition.reduced_definition_id is not None
        or armor.locations != ("torso",)
        or armor.split_dr
        or armor.front_only
        or armor.layer != 0
    ):
        raise ValidationError("Corrosion requires a single unsplit torso-only armor profile")
    projected_profile(state.resources, item, entry)
    previous = erosion(state.resources, item.id)
    return CorrosionProtection(
        actor_id=actor_id,
        item_id=item.id,
        definition_id=item.definition_id,
        profile_digest=profile_digest(entry),
        original_dr=armor.dr,
        previous_loss=0 if previous is None else previous.total_loss,
    )
