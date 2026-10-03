"""Private B485 rolled parts commitments, never a request-authored die."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError
from wayfarer.models import Id, Record

PREFIX = "armoury-parts:"


class AssessRepairParts(Command):
    kind: Literal["assess-repair-parts"] = "assess-repair-parts"
    item_id: Id
    repair_start_command_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)


class RepairPartsAssessment(Record):
    command_id: Id
    actor_id: Id
    assessor_id: Id
    item_id: Id
    definition_id: Id
    condition: ObjectCondition
    profile_digest: str
    parts_definition_id: Id
    parts_price_digest: str
    die: int = Field(ge=1, le=6)
    quantity: int = Field(ge=0)


def digest(entry: EquipmentProfile) -> str:
    return hashlib.sha256(entry.model_dump_json().encode()).hexdigest()


def latest(state: ResourceState, item_id: str) -> RepairPartsAssessment | None:
    return next(
        (
            RepairPartsAssessment.model_validate_json(e.kind)
            for e in reversed(state.events)
            if e.id.startswith(PREFIX)
            and RepairPartsAssessment.model_validate_json(e.kind).item_id == item_id
        ),
        None,
    )


def require_current(
    assessment: RepairPartsAssessment,
    *,
    actor_id: str,
    definition_id: str,
    condition: ObjectCondition,
    entry: EquipmentProfile,
    part_entry: EquipmentProfile,
) -> None:
    if (
        assessment.actor_id != actor_id
        or assessment.definition_id != definition_id
        or assessment.condition != condition
        or assessment.profile_digest != digest(entry)
        or assessment.parts_definition_id != part_entry.definition_id
        or assessment.parts_price_digest != digest(part_entry)
    ):
        raise ConflictError("Recorded repair parts requirement no longer matches current equipment")


def record(
    state: ResourceState, assessment: RepairPartsAssessment, command_id: str
) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=assessment.actor_id,
                    kind=assessment.model_dump_json(),
                ),
            )
        }
    )
