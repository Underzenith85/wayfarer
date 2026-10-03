"""Pinned repair work and recorded outcomes; shared time is advanced elsewhere."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.repair_time import RepairTimeSelection
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Id, Record


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
    # Exact skill/effect binding for source-bound Armoury restoration tasks.
    procedure_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    effect: str | None = Field(default=None, exclude_if=lambda value: value is None)
    skill_technology_level: int | None = Field(default=None, ge=0, le=12)
    equipment_technology_level: int | None = Field(default=None, ge=0, le=12)
    technology_level_penalty: int = Field(default=0, le=0)
    time_plan: RepairTimeSelection | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


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
