"""Caster-owned B253 findings and remembered source admission, never world reveal."""

import hashlib

from pydantic import Field

from wayfarer.engine.simulation.magic.spell_state import SpellResult
from wayfarer.engine.simulation.magic.water_effects import DISCOVERY_PREFIX, WaterDiscovery
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record


class WaterFinding(Record):
    direction: tuple[int, int] | None
    distance_yards: int | None
    nature: str | None


class WaterSpellResult(SpellResult):
    """Private service result; the public SpellResult schema remains closed."""

    finding: WaterFinding | None = Field(default=None, exclude_if=lambda value: value is None)


def findings(state: ResourceState, actor_id: str) -> tuple[tuple[int, WaterDiscovery], ...]:
    return tuple(
        (event.at, discovery)
        for event in state.events
        if event.id.startswith(DISCOVERY_PREFIX)
        if event.target_id == actor_id
        if (discovery := WaterDiscovery.model_validate_json(event.kind)).actor_id == actor_id
    )


def known_sources(state: ResourceState, actor_id: str) -> frozenset[str]:
    return frozenset(
        discovery.source_id
        for _, discovery in findings(state, actor_id)
        if discovery.source_id is not None
    )


def finding(discovery: WaterDiscovery) -> WaterFinding:
    return WaterFinding(
        direction=discovery.direction,
        distance_yards=discovery.distance_yards,
        nature=discovery.nature,
    )


def command_finding(state: ResourceState, command_id: str, actor_id: str) -> WaterFinding | None:
    identity = DISCOVERY_PREFIX + hashlib.sha256(command_id.encode()).hexdigest()
    event = next((e for e in state.events if e.id == identity and e.target_id == actor_id), None)
    if event is None:
        return None
    discovery = WaterDiscovery.model_validate_json(event.kind)
    return finding(discovery) if discovery.actor_id == actor_id else None


def projection(state: ResourceState, actor_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {"actor_id": actor_id, "at": at, **finding(discovery).model_dump(mode="json")}
        for actor_id in actor_ids
        for at, discovery in findings(state, actor_id)
    )
