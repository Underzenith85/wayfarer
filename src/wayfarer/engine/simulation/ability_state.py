"""Recorded supernatural ability state and read projections."""

from wayfarer.engine.simulation.ability_types import AbilityEffect, AbilityEvent
from wayfarer.engine.simulation.resources import ResourceState

PREFIX = "ability:"


def history(resources: ResourceState) -> tuple[tuple[int, AbilityEvent], ...]:
    return tuple(
        (event.at, AbilityEvent.model_validate_json(event.kind))
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def effects(resources: ResourceState) -> tuple[AbilityEffect, ...]:
    latest: dict[tuple[str, str], AbilityEffect] = {}
    for _, event in history(resources):
        if event.effect:
            latest[event.actor_id, event.ability_id] = event.effect
    return tuple(
        effect
        for effect in latest.values()
        if effect.active and (effect.expires_at is None or resources.game_time < effect.expires_at)
    )
