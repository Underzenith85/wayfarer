"""Immutable private B253 per-cast commitments; historical spell records unchanged."""

import hashlib

from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError
from wayfarer.models import Id, Record

PREFIX = "water-cast-plan:"


class WaterCastPlan(Record):
    cast_id: Id
    actor_id: Id
    channel_id: Id
    plan: WaterPlan


def recorded(state: ResourceState, cast_id: str) -> WaterCastPlan | None:
    event = next(
        (e for e in state.events if e.id == PREFIX + hashlib.sha256(cast_id.encode()).hexdigest()),
        None,
    )
    return WaterCastPlan.model_validate_json(event.kind) if event else None


def require_plan(state: ResourceState, value: WaterCastPlan, *, starting: bool) -> None:
    existing = recorded(state, value.cast_id)
    if existing is None and not starting:
        raise ConflictError("Water cast is missing its original material commitment")
    if existing is not None and existing != value:
        raise ConflictError("Water cast channel or material commitment changed")


def remember(state: ResourceState, value: WaterCastPlan) -> ResourceState:
    require_plan(state, value, starting=True)
    if recorded(state, value.cast_id) is not None:
        return state
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(value.cast_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=value.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )
