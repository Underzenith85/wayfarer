"""Persisted dismantling tasks independent of orchestration."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Id, Record


class SalvageTask(Record):
    id: Id
    actor_id: Id
    item_id: Id
    tool_id: Id
    definition_id: str
    condition: ObjectCondition
    start: int = Field(ge=0)
    due: int = Field(ge=0)
    skill: int
    status: Literal["pending", "completed", "cancelled"] = "pending"
    check_dice: tuple[int, ...] = ()
    recovered_item_id: Id | None = None
    recovered_quantity: int = Field(default=0, ge=0)


def tasks(state: ResourceState) -> tuple[SalvageTask, ...]:
    latest: dict[str, SalvageTask] = {}
    for event in state.events:
        if event.id.startswith("object-salvage:"):
            task = SalvageTask.model_validate_json(event.kind)
            latest[task.id] = task
    return tuple(latest.values())


def record(state: ResourceState, task: SalvageTask, command_id: str) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id="object-salvage:" + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=task.item_id,
                    kind=task.model_dump_json(),
                ),
            )
        }
    )
