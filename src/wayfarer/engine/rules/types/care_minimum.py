"""Private source-generation proof for one voluntary treatment's captured score."""

import hashlib
import json
from collections.abc import Iterable
from typing import Protocol

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "care-minimum:"
KINDS = frozenset({"first-aid", "physician", "resuscitate", "stabilize", "repair-lasting"})
_CAPTURED = (
    "id",
    "actor_id",
    "target_id",
    "profile_id",
    "kind",
    "procedure",
    "start",
    "due",
    "skill",
    "treatment_modifier",
    "wound_id",
    "technology_level",
)


class CareMinimum(Record):
    generation: int = Field(strict=True, ge=1, le=1)
    task_id: Id
    actor_id: Id
    target_id: Id
    start: int = Field(strict=True, ge=0)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CareEvent(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def at(self) -> int: ...
    @property
    def target_id(self) -> str | None: ...
    @property
    def kind(self) -> str: ...


def _digest(task: RecoveryTask) -> str:
    values = task.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps({key: values.get(key) for key in _CAPTURED}, sort_keys=True).encode()
    ).hexdigest()


def voluntary(task: RecoveryTask) -> bool:
    return task.procedure == "repair-lasting" or (task.procedure is None and task.kind in KINDS)


def producer(task: RecoveryTask) -> CareMinimum:
    if task.profile_id != "gurps-basic-set-4e-2004" or not voluntary(task):
        raise ValidationError("Medical minimum policy requires its exact voluntary producer")
    return CareMinimum(
        generation=1,
        task_id=task.id,
        actor_id=task.actor_id,
        target_id=task.target_id,
        start=task.start,
        snapshot_digest=_digest(task),
    )


def enabled(task: RecoveryTask, events: Iterable[CareEvent], now: int) -> bool:
    matches = []
    for event in events:
        if not event.id.startswith(PREFIX):
            continue
        try:
            value = CareMinimum.model_validate_json(event.kind)
        except SchemaError as exc:
            raise ValidationError("Malformed medical minimum producer") from exc
        if (
            event.id != PREFIX + hashlib.sha256(value.task_id.encode()).hexdigest()
            or event.at != value.start
            or event.target_id != value.actor_id
            or event.at > now
        ):
            raise ValidationError("Medical minimum producer has invalid provenance")
        if value.task_id == task.id:
            matches.append(value)
    if not matches:
        return False
    if len(matches) != 1:
        raise ValidationError("Duplicate medical minimum producer")
    if matches[0] != producer(task):
        raise ValidationError("Medical minimum producer does not match its captured task")
    return True
