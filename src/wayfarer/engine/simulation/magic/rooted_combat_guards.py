"""Explicit admission for unsupported B244 forced-displacement composition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def require_supported_impact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    target_id: str,
    damage_type: DamageType,
) -> None:
    if damage_type != "cr" or active_effect(state.resources, target_id) is None:
        return
    target = next(p for p in encounter.participants if p.actor_id == target_id)
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    if any(
        i.id in target.ready_item_ids
        and i.ready
        and i.equipped
        and entries[i.definition_id].shield is not None
        for i in state.resources.items
        if i.definition_id in entries
    ):
        raise ValidationError("Rooted Feet does not yet support mapped shield knockback")
