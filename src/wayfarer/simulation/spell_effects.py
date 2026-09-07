"""Consumers of executed spell records; no secondary duration or damage engine."""

from typing import TYPE_CHECKING

from wayfarer.errors import ValidationError
from wayfarer.simulation.resources import ResourceEvent, ResourceState
from wayfarer.simulation.spells import SpellEvent, SpellResult, active_spells, event_id

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState


def illuminated(state: PlayState, target_id: str) -> bool:
    """Torch-sized light (B249); geometry never reveals hidden knowledge by itself."""
    from wayfarer.simulation.combat import CombatEngine, GridPoint

    entities = {e.id: e for e in state.world.entities}
    target = entities.get(target_id)
    if target is None:
        return False
    for effect in active_spells(state.resources):
        if (
            not effect.execute_effects
            or effect.spell_id != "light"
            or target.location_id != effect.location_id
        ):
            continue
        if effect.position is None:
            if effect.target_id == target_id:
                return True
            continue
        encounter = next((e for e in state.encounters if e.id == effect.encounter_id), None)
        participant = (
            next((p for p in encounter.participants if p.actor_id == target_id), None)
            if encounter
            else None
        )
        if (
            participant
            and CombatEngine.distance(
                GridPoint(x=effect.position[0], y=effect.position[1]), participant.position
            )
            <= 2
        ):
            return True
    return False


def dazed(state: ResourceState, actor_id: str) -> bool:
    return any(
        e.execute_effects and e.spell_id == "daze" and e.target_id == actor_id
        for e in active_spells(state)
    )


def require_not_dazed(state: ResourceState, actor_id: str) -> None:
    if dazed(state, actor_id):
        raise ValidationError("Dazed actor cannot act or defend")


def break_daze(state: ResourceState, actor_id: str, command_id: str) -> ResourceState:
    events: list[ResourceEvent] = []
    for effect in active_spells(state):
        if effect.execute_effects and effect.spell_id == "daze" and effect.target_id == actor_id:
            events.append(
                ResourceEvent(
                    id=event_id(command_id + ":daze:" + effect.cast_id),
                    at=state.game_time,
                    target_id=actor_id,
                    kind=SpellEvent(
                        effect=effect.model_copy(update={"phase": "ended"}),
                        result=SpellResult(outcome="cancelled"),
                    ).model_dump_json(),
                )
            )
    return state.model_copy(update={"events": state.events + tuple(events)})
