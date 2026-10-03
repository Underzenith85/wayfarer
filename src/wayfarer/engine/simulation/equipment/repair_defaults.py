"""Private B168/B173/B178 default provenance; no caller-authored skill values."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.repair_time import Method
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

TRAINING_PREFIX = "armoury-training:"
DEFAULT_PREFIX = "armoury-default:"
TARGETS = frozenset(
    {"skill:armoury-melee-weapons", "skill:armoury-body-armor", "skill:armoury-small-arms"}
)


class DeclareArmouryTraining(Command):
    kind: Literal["declare-armoury-training"] = "declare-armoury-training"
    performer_id: Id
    build_revision: str
    personal_technology_level: int = Field(ge=0, le=12)
    society_known_skills: tuple[Id, ...]


class SelectRepairDefault(Command):
    kind: Literal["select-repair-default"] = "select-repair-default"
    item_id: Id
    start_command_id: Id
    source_id: Id
    repair_time_method: Method | None = Field(default=None, exclude_if=lambda value: value is None)


class ArmouryTraining(Record):
    command_id: Id
    performer_id: Id
    build_revision: str
    personal_technology_level: int = Field(ge=0, le=12)
    society_known_skills: tuple[Id, ...]


class RepairDefaultSelection(Record):
    command_id: Id
    actor_id: Id
    item_id: Id
    start_command_id: Id
    build_revision: str
    training_command_id: Id
    definition_id: Id
    equipment_digest: str
    condition: ObjectCondition
    skill_id: Id
    source_id: Id
    source_level: int
    default_modifier: int
    skill_level: int
    training_tl: int = Field(ge=0, le=12)
    equipment_tl: int = Field(ge=0, le=12)
    tl_penalty: int = Field(le=0)
    repair_time_method: Method | None = Field(default=None, exclude_if=lambda value: value is None)


def training(resources: ResourceState, actor_id: str) -> ArmouryTraining | None:
    return next(
        (
            p
            for e in reversed(resources.events)
            if e.id.startswith(TRAINING_PREFIX)
            and (p := ArmouryTraining.model_validate_json(e.kind)).performer_id == actor_id
        ),
        None,
    )


def selected(resources: ResourceState, start_command_id: str) -> RepairDefaultSelection | None:
    return next(
        (
            p
            for e in reversed(resources.events)
            if e.id.startswith(DEFAULT_PREFIX)
            and (p := RepairDefaultSelection.model_validate_json(e.kind)).start_command_id
            == start_command_id
        ),
        None,
    )


def record(
    resources: ResourceState, value: ArmouryTraining | RepairDefaultSelection
) -> ResourceState:
    prefix = TRAINING_PREFIX if isinstance(value, ArmouryTraining) else DEFAULT_PREFIX
    actor_id = value.performer_id if isinstance(value, ArmouryTraining) else value.actor_id
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=prefix + hashlib.sha256(value.command_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )
