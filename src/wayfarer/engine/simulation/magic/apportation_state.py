"""Private B251 targeting, measured world routes and command receipts."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

CHANNEL = "apportation-channel:"
ROUTE = "apportation-route:"
RECEIPT = "apportation-host:"


class ApportationChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    mana: ManaLevel = "normal"
    distance_yards: int = Field(default=0, ge=0, le=10000)
    visible: bool = True
    body_weight_millipounds: int | None = Field(default=None, ge=1)


class ApportationRoute(Record):
    id: Id
    source_id: Id
    destination_id: Id
    distance_yards: int = Field(ge=1, le=60)


class DeclareApportationChannel(Command):
    kind: Literal["declare-apportation-channel"] = "declare-apportation-channel"
    channel: ApportationChannel


class DeclareApportationRoute(Command):
    kind: Literal["declare-apportation-route"] = "declare-apportation-route"
    route: ApportationRoute


class CastApportation(Command):
    kind: Literal["cast-apportation"] = "cast-apportation"
    operation: Literal["start", "concentrate", "complete", "maintain", "cancel"]
    channel_id: Id
    cast_id: Id


class MoveApportation(Command):
    kind: Literal["move-apportation"] = "move-apportation"
    channel_id: Id
    cast_id: Id
    route_id: Id


ApportationCommand = Annotated[
    DeclareApportationChannel | DeclareApportationRoute | CastApportation | MoveApportation,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[ApportationCommand] = TypeAdapter(ApportationCommand)


class ApportationReceipt(Record):
    command_id: Id
    outcome: str
    target_id: Id
    energy_spent: int = 0
    game_time: int


def save(
    resources: ResourceState, prefix: str, command_id: str, target_id: str, value: Record
) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=prefix + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=target_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def channels(resources: ResourceState) -> tuple[ApportationChannel, ...]:
    return tuple(
        ApportationChannel.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(CHANNEL)
    )


def routes(resources: ResourceState) -> tuple[ApportationRoute, ...]:
    return tuple(
        ApportationRoute.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(ROUTE)
    )
