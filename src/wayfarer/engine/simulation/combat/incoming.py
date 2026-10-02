"""Resolve a pending incoming source without assuming it is an inventory item."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.rules_context import RulesContext


def incoming_mode(
    runtime: RulesContext, state: PlayState, encounter: Encounter, pending: PendingDefense
) -> MeleeMode | RangedMode | None:
    if pending.composed_attack_id is not None:
        # deferred: composed authority depends on RulesContext, which constructs CombatEngine.
        from wayfarer.engine.simulation.traits.composed_sources import pending_binding

        pending_binding(state.resources, encounter, pending)
        return None
    if pending.spell_cast_id is not None:
        return None
    return mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)


def incoming_ranged(
    runtime: RulesContext, state: PlayState, encounter: Encounter, pending: PendingDefense
) -> bool:
    value = incoming_mode(runtime, state, encounter, pending)
    return (
        pending.composed_attack_id is not None
        or pending.spell_cast_id is not None
        or isinstance(value, RangedMode)
    )
