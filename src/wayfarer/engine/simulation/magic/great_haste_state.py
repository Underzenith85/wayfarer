"""Private source-bound Great Haste channels and single end-fatigue witnesses."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

CHANNEL = "great-haste-channel:"
ACTIVATION = "great-haste-active:"
ENDED = "great-haste-ended:"
RECEIPT = "great-haste-host:"


class GreatHasteChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    mana: ManaLevel = "normal"
    distance_yards: int = Field(default=0, ge=0, le=10000)
    visible: bool = True


class DeclareGreatHasteChannel(Command):
    kind: Literal["declare-great-haste-channel"] = "declare-great-haste-channel"
    channel: GreatHasteChannel


class CastGreatHaste(Command):
    kind: Literal["cast-great-haste"] = "cast-great-haste"
    operation: Literal["start", "concentrate", "complete", "cancel"]
    channel_id: Id
    cast_id: Id


GreatHasteCommand = Annotated[
    DeclareGreatHasteChannel | CastGreatHaste, Field(discriminator="kind")
]
ADAPTER: TypeAdapter[GreatHasteCommand] = TypeAdapter(GreatHasteCommand)


class GreatHasteActivation(Record):
    cast_id: Id
    actor_id: Id
    target_id: Id
    expires_at: int = Field(ge=0)
    ht: int = Field(ge=1)
    will: int = Field(ge=1)


class GreatHasteEnd(Record):
    cast_id: Id
    fp_lost: int = Field(ge=0)
    hp_lost: int = Field(ge=0)


class GreatHasteReceipt(Record):
    command_id: Id
    outcome: str
    energy_spent: int = 0
    game_time: int


def save(
    resources: ResourceState, prefix: str, identifier: str, target_id: str, value: Record
) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=prefix + hashlib.sha256(identifier.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=target_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def channels(resources: ResourceState) -> tuple[GreatHasteChannel, ...]:
    return tuple(
        GreatHasteChannel.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(CHANNEL)
    )
