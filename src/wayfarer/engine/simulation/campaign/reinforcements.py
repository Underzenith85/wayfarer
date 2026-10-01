"""Shared subgroup admission for one arriving combatant, without moving companions."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import group_for
from wayfarer.errors import ConflictError, ValidationError


def admit_actor(state: PlayState, joining_actor_id: str, target_actor_id: str) -> PlayState:
    """Canonical reinforcement clock/scene checks and single-actor transfer."""
    source, target = group_for(state, joining_actor_id), group_for(state, target_actor_id)
    if source.scene_id != target.scene_id or source.paused or target.paused:
        raise ValidationError("Reinforcements must be in the encounter scene")
    if (
        source.ready_through != state.resources.game_time
        or target.ready_through != state.resources.game_time
        or any(q.group_id in (source.id, target.id) for q in state.party.queue)
    ):
        raise ConflictError("Reinforcement arrival requires synchronized time")
    if source.id == target.id:
        return state
    remaining = tuple(a for a in source.actor_ids if a != joining_actor_id)
    groups = tuple(
        g.model_copy(
            update={"actor_ids": g.actor_ids + (joining_actor_id,), "generation": g.generation + 1}
        )
        if g.id == target.id
        else g.model_copy(update={"actor_ids": remaining, "generation": g.generation + 1})
        if g.id == source.id
        else g
        for g in state.party.groups
        if g.id != source.id or remaining
    )
    return state.model_copy(update={"party": state.party.model_copy(update={"groups": groups})})
