"""Private producer generations retained across deferred queued checks and rulings."""

import hashlib
from typing import Literal

from pydantic import field_validator

from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "skill-prerequisite-context:"
Producer = Literal["queued-activity", "ruling"]


class CapturedSkillContext(Record):
    producer: Producer
    command_id: Id
    actor_id: Id
    generation: Literal[1]

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported captured skill-context generation")
        return value


def identifier(producer: Producer, command_id: str) -> str:
    return PREFIX + hashlib.sha256(f"{producer}:{command_id}".encode()).hexdigest()


def mark(
    resources: ResourceState, producer: Producer, command_id: str, actor_id: str, *, enabled: bool
) -> ResourceState:
    if not enabled:
        return resources
    event_id = identifier(producer, command_id)
    if any(e.id == event_id for e in resources.events):
        raise ConflictError("Skill-context producer identity already exists")
    value = CapturedSkillContext(
        producer=producer, command_id=command_id, actor_id=actor_id, generation=1
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def enabled(resources: ResourceState, producer: Producer, command_id: str, actor_id: str) -> bool:
    event = next((e for e in resources.events if e.id == identifier(producer, command_id)), None)
    if event is None:
        return False
    value = CapturedSkillContext.model_validate_json(event.kind)
    expected = CapturedSkillContext(
        producer=producer, command_id=command_id, actor_id=actor_id, generation=1
    )
    if value != expected or event.target_id != actor_id or event.at > resources.game_time:
        raise ValidationError("Captured skill context does not match its deferred producer")
    return True
