"""Reserve and settle one explicitly selected, internally timed repair tool.

Charge units are the active seconds already used by GeneralEquipmentFeature.
No source-wide Armoury power rate or external consumable conversion is inferred.
"""

import hashlib

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.equipment.repairs import RepairTask, tasks
from wayfarer.engine.simulation.resources import Item, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "armoury-tool-operation:"


class RepairToolOperation(Record):
    start_command_id: Id
    actor_id: Id
    item_id: Id
    tool_id: Id
    definition_id: Id
    equipment_digest: str
    start: int = Field(ge=0, strict=True)
    duration_seconds: int = Field(gt=0, strict=True)
    charges: int = Field(ge=0, strict=True)


def timed_feature(entry: EquipmentProfile, item: Item) -> GeneralEquipmentFeature | None:
    """Only a single plain, internally charged tool has a proved use contract."""
    features = tuple(f for f in entry.general if f.kind == "tool")
    if any(f.modifier != 0 or f.consumable_definition_id is not None for f in features):
        raise ValidationError(
            "Observed tools require a basic pinned profile without operating limits"
        )
    timed = tuple(f for f in features if f.duration_seconds is not None)
    if not timed:
        return None
    if (
        len(entry.general) != 1
        or item.quantity != 1
        or item.charges is None
        or entry.power_cell_capacity is not None
        or entry.electronics is not None
        or entry.ammunition
    ):
        raise ValidationError(
            "Observed tools require a basic pinned profile without operating limits"
        )
    return timed[0]


def _has_operating_limits(entry: EquipmentProfile) -> bool:
    return (
        entry.electronics is not None
        or entry.power_cell_capacity is not None
        or any(
            feature.duration_seconds is not None
            or feature.consumable_definition_id is not None
            or feature.capacity is not None
            for feature in entry.general
        )
    )


def _plain_timed_tool(entry: EquipmentProfile, tool: Item, skill_id: str | None) -> bool:
    if (
        len(entry.general) != 1
        or not skill_id
        or tool.quantity != 1
        or tool.charges is None
        or entry.electronics is not None
        or entry.power_cell_capacity is not None
        or entry.ammunition
    ):
        return False
    feature = entry.general[0]
    return feature.duration_seconds is not None and feature == GeneralEquipmentFeature(
        kind="tool", skill_id=skill_id, duration_seconds=feature.duration_seconds, note=feature.note
    )


def require_selection(
    entry: EquipmentProfile,
    tool: Item,
    selected: bool,
    *,
    source_policy: bool,
    skill_id: str | None,
) -> None:
    """Classify every declared operating limit before accepting a fresh repair."""
    if (
        source_policy
        and _has_operating_limits(entry)
        and (not selected or not _plain_timed_tool(entry, tool, skill_id))
    ):
        raise ValidationError(
            "Repair toolkit operating limits require explicit supported selection"
        )
    if (
        not selected
        and tool.charges is not None
        and entry.power_cell_capacity is None
        and entry.electronics is None
        and any(
            feature.kind == "tool"
            and feature.duration_seconds is not None
            and feature.consumable_definition_id is None
            for feature in entry.general
        )
    ):
        raise ValidationError("Internally timed repair tools require explicit selection")


def prepare(
    entry: EquipmentProfile,
    tool: Item,
    actor_id: str,
    task_id: str,
    item_id: str,
    start: int,
    seconds: int,
    skill_id: str,
) -> RepairToolOperation | None:
    feature = timed_feature(entry, tool)
    if feature is None:
        return None
    if feature.skill_id != skill_id:
        raise ValidationError("Internally timed repair tools require an exact repair skill binding")
    assert feature.duration_seconds is not None and tool.charges is not None
    if seconds > feature.duration_seconds or seconds > tool.charges:
        raise ValidationError("Repair tool has insufficient operating time or charge")
    return RepairToolOperation(
        start_command_id=task_id,
        actor_id=actor_id,
        item_id=item_id,
        tool_id=tool.id,
        definition_id=tool.definition_id,
        equipment_digest=digest(entry),
        start=start,
        duration_seconds=seconds,
        charges=tool.charges,
    )


def record(
    resources: ResourceState, value: RepairToolOperation | None, preview: bool
) -> ResourceState:
    if value is None or preview:
        return resources
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(value.start_command_id.encode()).hexdigest(),
                    at=value.start,
                    target_id=value.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def current(resources: ResourceState, task: RepairTask) -> RepairToolOperation | None:
    matches = []
    for event in resources.events:
        if not event.id.startswith(PREFIX):
            continue
        try:
            value = RepairToolOperation.model_validate_json(event.kind)
        except SchemaError as exc:
            raise ValidationError("Malformed repair tool operation") from exc
        if (
            event.id != PREFIX + hashlib.sha256(value.start_command_id.encode()).hexdigest()
            or event.at != value.start
            or event.target_id != value.actor_id
            or event.at > resources.game_time
        ):
            raise ValidationError("Repair tool operation has invalid provenance")
        if value.start_command_id == task.id:
            matches.append(value)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValidationError("Repair tool operation has duplicate producer records")
    value = matches[0]
    if (
        value.actor_id != task.actor_id
        or value.item_id != task.item_id
        or value.tool_id != task.tool_id
        or value.start != task.start
        or value.duration_seconds != task.due - task.start
        or value.charges < value.duration_seconds
    ):
        raise ValidationError("Repair tool operation does not match its producer task")
    return value


def reserved(resources: ResourceState, tool_id: str) -> bool:
    return any(
        task.tool_id == tool_id
        and task.status == "pending"
        and current(resources, task) is not None
        for task in tasks(resources)
    )


def settle(
    resources: ResourceState,
    task: RepairTask,
    entries: tuple[EquipmentProfile, ...],
    *,
    verify_profile: bool = True,
) -> ResourceState:
    value = current(resources, task)
    if value is None:
        return resources
    entry = next((e for e in entries if e.definition_id == value.definition_id), None)
    if verify_profile and (entry is None or digest(entry) != value.equipment_digest):
        raise ConflictError("Reserved repair tool profile changed")
    tool = next((i for i in resources.items if i.id == value.tool_id), None)
    if (
        tool is None
        or tool.definition_id != value.definition_id
        or tool.quantity != 1
        or tool.charges != value.charges
    ):
        raise ConflictError("Reserved repair tool operating reservoir changed")
    disabled_commands = {
        result.command_id
        for result in resources.object_results
        if result.item_id == value.tool_id and result.condition.disabled
    }
    producer_id = PREFIX + hashlib.sha256(task.id.encode()).hexdigest()
    producer_index = next(
        index for index, event in enumerate(resources.events) if event.id == producer_id
    )
    stopped = min(
        (
            event.at
            for event in resources.events[producer_index + 1 :]
            if event.id.startswith("object:")
            and event.id.removeprefix("object:") in disabled_commands
            and event.target_id == value.tool_id
            and event.at >= value.start
        ),
        default=resources.game_time,
    )
    seconds = min(max(0, stopped - value.start), value.duration_seconds)
    return resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"charges": value.charges - seconds})
                if item.id == value.tool_id
                else item
                for item in resources.items
            )
        }
    )
