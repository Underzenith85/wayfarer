"""Consumers of executed spell records; no secondary duration or damage engine."""

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import point_distance
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.spell_state import SpellEffect, active_spells
from wayfarer.engine.simulation.magic.spell_state import break_daze as break_daze
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState


def lights(state: PlayState, target_id: str, *, reversed: bool = False) -> tuple[SpellEffect, ...]:
    """Local candle light (B249); illumination does not disclose hidden entities."""
    entities = {e.id: e for e in state.world.entities}
    target = entities.get(target_id)
    if target is None:
        return ()
    found = []
    for effect in active_spells(state.resources):
        if (
            not effect.execute_effects
            or effect.spell_id != "light"
            or effect.reversed != reversed
            or target.location_id != effect.location_id
        ):
            continue
        if effect.position is None:
            if effect.target_id == target_id:
                found.append(effect)
            continue
        encounter = next((e for e in state.encounters if e.id == effect.encounter_id), None)
        participant = (
            next((p for p in encounter.participants if p.actor_id == target_id), None)
            if encounter
            else None
        )
        if (
            participant
            and point_distance(
                Hex(q=effect.position[0], r=effect.position[1])
                if effect.geometry == "hex"
                else GridPoint(x=effect.position[0], y=effect.position[1]),
                participant.position,
            )
            <= effect.light_radius
        ):
            found.append(effect)
    return tuple(found)


def illuminated(state: PlayState, target_id: str, *, reversed: bool = False) -> bool:
    return bool(lights(state, target_id, reversed=reversed))


def dazed(state: ResourceState, actor_id: str) -> bool:
    return any(
        e.execute_effects and e.spell_id == "daze" and e.target_id == actor_id
        for e in active_spells(state)
    )


def require_not_dazed(state: ResourceState, actor_id: str) -> None:
    if dazed(state, actor_id):
        raise ValidationError("Dazed actor cannot act or defend")


def lighting_penalty(state: PlayState, target_id: str, darkness: int) -> int:
    if illuminated(state, target_id, reversed=True):
        return -10
    if not illuminated(state, target_id):
        return darkness
    return max(
        darkness,
        max(0 if e.execution_version == 1 else e.light_penalty for e in lights(state, target_id)),
    )
