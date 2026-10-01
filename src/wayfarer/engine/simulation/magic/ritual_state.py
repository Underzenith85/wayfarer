"""Private director observations for B237 capabilities not encoded by anatomy."""

import hashlib

from pydantic import Field, model_validator

from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "spell-ritual:"


class DeclareRitualCapability(Command):
    speech_available: bool | None = None
    gesture_available: bool | None = None
    full_body_free: bool | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def consistent(self) -> DeclareRitualCapability:
        if self.full_body_free and self.gesture_available is False:
            raise ValueError("A free full-body ritual cannot forbid every gesture")
        return self


class RitualCapability(Record):
    command: DeclareRitualCapability
    build_revision: Id


def identifier(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def latest(state: ResourceState, actor_id: str) -> RitualCapability | None:
    return next(
        (
            RitualCapability.model_validate_json(e.kind)
            for e in reversed(state.events)
            if e.id.startswith(PREFIX) and e.target_id == actor_id
        ),
        None,
    )


def save(state: ResourceState, value: RitualCapability) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=identifier(value.command.id),
                    at=state.game_time,
                    target_id=value.command.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )
