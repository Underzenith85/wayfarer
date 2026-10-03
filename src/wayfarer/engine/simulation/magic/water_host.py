"""Private director declarations for real placed water and approved channels."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_bindings import declare as declare_channel
from wayfarer.engine.simulation.magic.water_collection import (
    CollectionReceipt,
    CollectWater,
    DeclareWaterCollection,
    collect,
)
from wayfarer.engine.simulation.magic.water_collection import (
    declare as declare_collection,
)
from wayfarer.engine.simulation.magic.water_mist import DeclareWaterScene
from wayfarer.engine.simulation.magic.water_mist import declare as declare_scene
from wayfarer.engine.simulation.magic.water_parcels import (
    DeclareWaterParcels,
    reject_aggregate_alias,
)
from wayfarer.engine.simulation.magic.water_parcels import declare as declare_parcels
from wayfarer.engine.simulation.magic.water_state import WaterBody, declare
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.models import Id, Record

PREFIX = "water-host:"


class DeclareWater(Command):
    kind: Literal["declare-water"] = "declare-water"
    body: WaterBody


class DeclareWaterChannel(Command):
    kind: Literal["declare-water-channel"] = "declare-water-channel"
    channel: WaterChannel


WaterHostCommand = Annotated[
    DeclareWater
    | DeclareWaterChannel
    | DeclareWaterScene
    | DeclareWaterParcels
    | DeclareWaterCollection
    | CollectWater,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[WaterHostCommand] = TypeAdapter(WaterHostCommand)


class WaterReceipt(Record):
    command_id: Id
    outcome: Literal["declared"] = "declared"


def receipt_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def apply_host(
    runtime: RulesContext, state: PlayState, command: WaterHostCommand
) -> tuple[PlayState, WaterReceipt | CollectionReceipt]:
    if isinstance(command, CollectWater):
        return collect(runtime, state, command)
    if isinstance(command, DeclareWaterCollection):
        resources = declare_collection(state, command)
    elif isinstance(command, DeclareWater):
        reject_aggregate_alias(state.resources, command.body.object_id)
        resources = declare(state.world, state.resources, command.body, command.id)
    elif isinstance(command, DeclareWaterParcels):
        resources = declare_parcels(state.world, state.resources, command)
    elif isinstance(command, DeclareWaterScene):
        resources = declare_scene(state, command)
    else:
        runtime.approved_build(state, command.channel.actor_id)
        resources = declare_channel(state, command.channel, command.id)
    receipt = WaterReceipt(command_id=command.id)
    resources = resources.model_copy(
        update={
            "revision": state.revision + 1,
            "events": resources.events
            + (
                ResourceEvent(
                    id=receipt_id(command.id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=receipt.model_dump_json(),
                ),
            ),
        }
    )
    return state.model_copy(
        update={"revision": state.revision + 1, "resources": resources}
    ), receipt
