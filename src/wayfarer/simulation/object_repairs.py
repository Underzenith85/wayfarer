"""Pinned repair work and recorded outcomes; shared time is advanced elsewhere."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.models import Id, Record
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.object_types import ObjectCondition
from wayfarer.simulation.resources import ResourceEvent, ResourceState


class RepairTask(Record):
    id: Id
    actor_id: Id
    item_id: Id
    tool_id: Id
    start: int = Field(ge=0)
    due: int = Field(ge=0)
    skill: int
    condition: ObjectCondition
    parts_die: int | None = Field(default=None, ge=1, le=6)
    parts_quantity: int = Field(default=0, ge=0)
    status: Literal["pending", "completed", "cancelled"] = "pending"
    check: CheckTrace | None = None
    restored_hp: int = Field(default=0, ge=0)


def tasks(state: ResourceState) -> tuple[RepairTask, ...]:
    latest: dict[str, RepairTask] = {}
    for event in state.events:
        if event.id.startswith("object-repair:"):
            task = RepairTask.model_validate_json(event.kind)
            latest[task.id] = task
    return tuple(latest.values())


def record(state: ResourceState, task: RepairTask, command_id: str) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id="object-repair:" + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=task.item_id,
                    kind=task.model_dump_json(),
                ),
            )
        }
    )
