"""Persisted control grants, separate from static campaign membership."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record

PREFIX = "mental-control:"


class MentalControl(Record):
    controller_id: str
    target_id: str
    effect_id: str
    kind: Literal["influence", "possession"]
    expires_at: int | None = Field(default=None, ge=0)
    released: bool = False
    victory_margin: int = Field(ge=1)


def control_grants(resources: ResourceState) -> tuple[MentalControl, ...]:
    latest: dict[str, MentalControl] = {}
    for event in resources.events:
        if event.id.startswith(PREFIX):
            grant = MentalControl.model_validate_json(event.kind)
            latest[grant.effect_id] = grant
    return tuple(
        grant
        for grant in latest.values()
        if not grant.released
        and grant.effect_id in resources.active_effect_ids
        and (grant.expires_at is None or resources.game_time < grant.expires_at)
    )


def controlling_actor(resources: ResourceState, actor_id: str) -> str | None:
    grants = control_grants(resources)
    selected = next((grant for grant in reversed(grants) if grant.target_id == actor_id), None)
    if selected is not None:
        return selected.controller_id
    if any(grant.kind == "possession" and grant.controller_id == actor_id for grant in grants):
        # Consciousness is in the host; the previous body cannot take independent actions.
        return None
    return actor_id
