"""Private, explicit Haste targeting and B482 item magnitude metadata."""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

CHANNEL = "haste-channel:"
ITEM = "haste-item:"
MANA = "haste-mana:"
SWITCH = "haste-switch:"
RECEIPT = "haste-host:"


class HasteChannel(Record):
    id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    spell_id: Literal["haste"] = "haste"
    mana: ManaLevel = "normal"
    distance_yards: int = Field(default=0, ge=0, le=10000)
    visible: bool = True
    touching: bool = False
    magic_item_id: Id | None = None

    @model_validator(mode="after")
    def self_item(self) -> HasteChannel:
        if self.magic_item_id is not None and self.actor_id != self.target_id:
            raise ValueError("B482 Haste items affect only their user")
        return self


class HasteItem(Record):
    item_id: Id
    binding_id: Id
    definition_id: Id
    levels: int = Field(ge=1, le=3)
    form: Literal["clothing", "jewelry"]


class HasteMana(Record):
    location_id: Id
    mana: ManaLevel


class HasteSwitch(Record):
    item_id: Id
    binding_id: Id
    enabled: bool


class DeclareHasteChannel(Command):
    kind: Literal["declare-haste-channel"] = "declare-haste-channel"
    channel: HasteChannel


class DeclareHasteItem(Command):
    kind: Literal["declare-haste-item"] = "declare-haste-item"
    item: HasteItem


class ObserveHasteMana(Command):
    kind: Literal["observe-haste-mana"] = "observe-haste-mana"
    environment: HasteMana


class SwitchHasteItem(Command):
    kind: Literal["switch-haste-item"] = "switch-haste-item"
    switch: HasteSwitch


class HasteReceipt(Record):
    command_id: Id
    outcome: Literal["declared", "observed", "on", "off"]


def record(
    resources: ResourceState, prefix: str, command_id: str, target: str, value: Record
) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=prefix + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=target,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def channels(resources: ResourceState) -> tuple[HasteChannel, ...]:
    return tuple(
        HasteChannel.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(CHANNEL)
    )


def items(resources: ResourceState) -> tuple[HasteItem, ...]:
    return tuple(
        HasteItem.model_validate_json(e.kind) for e in resources.events if e.id.startswith(ITEM)
    )


def environments(resources: ResourceState) -> dict[str, ManaLevel]:
    return {
        v.location_id: v.mana
        for e in resources.events
        if e.id.startswith(MANA)
        for v in (HasteMana.model_validate_json(e.kind),)
    }


def enabled(resources: ResourceState, item: HasteItem) -> bool:
    values = tuple(
        HasteSwitch.model_validate_json(e.kind) for e in resources.events if e.id.startswith(SWITCH)
    )
    return next(
        (
            v.enabled
            for v in reversed(values)
            if (v.item_id, v.binding_id) == (item.item_id, item.binding_id)
        ),
        True,
    )
