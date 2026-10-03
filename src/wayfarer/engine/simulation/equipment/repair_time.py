"""Private B346 selected work methods, bound to one actual repair start."""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.resources import Command, Item, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "armoury-time:"
Method = Literal[
    "ordinary",
    "extra-2",
    "extra-4",
    "extra-8",
    "extra-15",
    "extra-30",
    "haste-10",
    "haste-20",
    "haste-30",
    "haste-40",
    "haste-50",
    "haste-60",
    "haste-70",
    "haste-80",
    "haste-90",
]
METHODS: dict[str, tuple[int, int]] = {
    "ordinary": (1800, 0),
    "extra-2": (3600, 1),
    "extra-4": (7200, 2),
    "extra-8": (14400, 3),
    "extra-15": (27000, 4),
    "extra-30": (54000, 5),
    **{f"haste-{n}": (1800 * (100 - n) // 100, -n // 10) for n in range(10, 100, 10)},
}


class SelectRepairTime(Command):
    kind: Literal["select-repair-time"] = "select-repair-time"
    item_id: Id
    start_command_id: Id
    method: Method


class RepairTimeSelection(Record):
    command_id: Id
    actor_id: Id
    item_id: Id
    start_command_id: Id
    definition_id: Id
    equipment_digest: str
    condition: ObjectCondition
    method: Method
    duration_seconds: int = Field(gt=0)
    modifier: int

    @model_validator(mode="after")
    def canonical_method(self) -> RepairTimeSelection:
        if (self.duration_seconds, self.modifier) != METHODS[self.method]:
            raise ValueError("Repair time plan must match its exact source method")
        return self


def selected(resources: ResourceState, start_command_id: str) -> RepairTimeSelection | None:
    return next(
        (
            p
            for e in reversed(resources.events)
            if e.id.startswith(PREFIX)
            and (p := RepairTimeSelection.model_validate_json(e.kind)).start_command_id
            == start_command_id
        ),
        None,
    )


def require_current(
    plan: RepairTimeSelection, actor_id: str, item: Item, entry: EquipmentProfile
) -> None:
    if (
        plan.actor_id != actor_id
        or plan.item_id != item.id
        or plan.definition_id != item.definition_id
        or plan.condition != item.condition
        or plan.equipment_digest != digest(entry)
    ):
        raise ConflictError("Selected repair time no longer matches current equipment")


def start_plan(
    resources: ResourceState,
    actor_id: str,
    item: Item,
    entry: EquipmentProfile,
    command_id: str,
    procedure_id: str | None,
    assessment_only: bool,
) -> RepairTimeSelection | None:
    if assessment_only:
        return None
    plan = selected(resources, command_id)
    if plan is not None:
        if procedure_id is None:
            raise ValidationError("Selected repair time requires a supported Armoury consumer")
        require_current(plan, actor_id, item, entry)
    return plan


def modifier(plan: RepairTimeSelection | None) -> int:
    return plan.modifier if plan else 0


def duration(plan: RepairTimeSelection | None) -> int:
    return plan.duration_seconds if plan else 1800


def finish_plan(
    plan: RepairTimeSelection | None, actor_id: str, item: Item, entry: EquipmentProfile
) -> None:
    if plan is not None:
        require_current(plan, actor_id, item, entry)


def record(resources: ResourceState, plan: RepairTimeSelection) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(plan.command_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=plan.actor_id,
                    kind=plan.model_dump_json(),
                ),
            )
        }
    )
