"""Private immutable lock targeting and placement facts."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Id, Record

PREFIX = "lock-channel:"


class LockChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    spell_id: Literal["lockmaster", "magelock"]
    distance_yards: int = Field(default=0, ge=0, le=10000)
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    visible: bool = True
    touching: bool = False
    encounter_id: Id | None = None
    position: tuple[int, int] | None = None
    geometry: Literal["square", "hex"] = "square"

    @model_validator(mode="after")
    def positioned(self) -> LockChannel:
        if (self.encounter_id is None) != (self.position is None):
            raise ValueError("Combat lock channels require an encounter and object placement")
        return self


def channels(state: ResourceState) -> tuple[LockChannel, ...]:
    return tuple(
        LockChannel.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )
