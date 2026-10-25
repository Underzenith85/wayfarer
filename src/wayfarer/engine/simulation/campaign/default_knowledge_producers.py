"""B173 actor adoption retained across deferred queued checks and rulings."""

import hashlib
from typing import Literal

from pydantic import field_validator

from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "skill-default-producer:"
Producer = Literal["queued-activity", "ruling"]


class CapturedDefaultKnowledge(Record):
    producer: Producer
    command_id: Id
    actor_id: Id
    generation: Literal[1, 2]

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> int:
        if type(value) is not int or value not in (1, 2):
            raise ValueError("Unsupported captured default-knowledge generation")
        return value


def identifier(producer: Producer, command_id: str) -> str:
    return PREFIX + hashlib.sha256(f"{producer}:{command_id}".encode()).hexdigest()


def mark(
    resources: ResourceState,
    producer: Producer,
    command_id: str,
    actor_id: str,
    *,
    enabled: bool,
    required: bool = False,
) -> ResourceState:
    if not enabled:
        return resources
    event_id = identifier(producer, command_id)
    if any(e.id == event_id for e in resources.events):
        raise ConflictError("Default knowledge producer identity already exists")
    value = CapturedDefaultKnowledge(
        producer=producer, command_id=command_id, actor_id=actor_id, generation=2 if required else 1
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


def generation(resources: ResourceState, producer: Producer, command_id: str, actor_id: str) -> int:
    events = tuple(e for e in resources.events if e.id == identifier(producer, command_id))
    if not events:
        return 0
    if len(events) != 1:
        raise ValidationError("Captured default knowledge producer identity is duplicated")
    event = events[0]
    value = CapturedDefaultKnowledge.model_validate_json(event.kind)
    expected = CapturedDefaultKnowledge(
        producer=producer, command_id=command_id, actor_id=actor_id, generation=value.generation
    )
    if value != expected or event.target_id != actor_id or event.at > resources.game_time:
        raise ValidationError("Captured skill context does not match its deferred producer")
    return value.generation


def enabled(resources: ResourceState, producer: Producer, command_id: str, actor_id: str) -> bool:
    return generation(resources, producer, command_id, actor_id) > 0


def required(resources: ResourceState, producer: Producer, command_id: str, actor_id: str) -> bool:
    return generation(resources, producer, command_id, actor_id) >= 2
