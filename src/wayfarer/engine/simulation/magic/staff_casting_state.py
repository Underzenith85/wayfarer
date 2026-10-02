"""Private cast intentions and expiring physical Staff contact observations."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellId
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState

INTENT_PREFIX = "staff-casting-intent:"
TOUCH_PREFIX = "staff-casting-touch:"
INVALIDATION_PREFIX = "staff-casting-invalid:"


class DeclareStaffIntent(Command):
    kind: Literal["declare"] = "declare"
    cast_id: Id
    spell_id: RuntimeSpellId
    channel_id: Id
    item_id: Id
    pointing: bool = False
    radius: int | None = Field(default=None, ge=1, le=100, exclude_if=lambda value: value is None)


class ObserveStaffTouch(Command):
    kind: Literal["touch"] = "touch"
    cast_id: Id
    touching: bool = True


StaffCastingCommand = Annotated[DeclareStaffIntent | ObserveStaffTouch, Field(discriminator="kind")]
ADAPTER: TypeAdapter[StaffCastingCommand] = TypeAdapter(StaffCastingCommand)


class StaffIntent(Record):
    command_id: Id
    actor_id: Id
    cast_id: Id
    spell_id: RuntimeSpellId
    channel_id: Id
    target_id: Id
    item_id: Id
    pointing: bool
    area: AreaSelection | None = Field(default=None, exclude_if=lambda value: value is None)
    radius: int | None = Field(default=None, ge=1, le=100, exclude_if=lambda value: value is None)


class StaffTouch(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    target_id: Id
    item_id: Id
    encounter_id: Id | None
    observed_by: Id
    declared_revision: int
    scope_digest: str
    touching: bool


class StaffTouchInvalidation(Record):
    observation_id: Id
    revision: int


def identifier(prefix: str, command_id: str) -> str:
    return prefix + hashlib.sha256(command_id.encode()).hexdigest()


def intents(resources: ResourceState) -> tuple[StaffIntent, ...]:
    return tuple(
        StaffIntent.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(INTENT_PREFIX)
    )


def observations(resources: ResourceState) -> tuple[StaffTouch, ...]:
    return tuple(
        StaffTouch.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(TOUCH_PREFIX)
    )


def invalidated(resources: ResourceState) -> frozenset[str]:
    return frozenset(
        StaffTouchInvalidation.model_validate_json(e.kind).observation_id
        for e in resources.events
        if e.id.startswith(INVALIDATION_PREFIX)
    )


def record(resources: ResourceState, value: StaffIntent | StaffTouch) -> ResourceState:
    prefix = INTENT_PREFIX if isinstance(value, StaffIntent) else TOUCH_PREFIX
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=identifier(prefix, value.command_id),
                    at=resources.game_time,
                    target_id=value.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def scope_digest(state: PlayState, intent: StaffIntent) -> str:
    # No clock, turn number, unrelated actor or spell state: stationary casting
    # does not require the GM to repeat the same physical observation each second.
    # deferred: combat movement imports this state module before grip helpers load.
    from wayfarer.engine.simulation.combat.objects.locations import item_hands

    # deferred: health helpers share the combat actor imports used by grip resolution.
    from wayfarer.engine.simulation.health.hit_locations import disabled

    entities = {e.id: e for e in state.world.entities}
    item = next((i for i in state.resources.items if i.id == intent.item_id), None)
    actor = next(a for a in state.actors if a.actor_id == intent.actor_id)
    scoped_actors = (
        (intent.actor_id,) if intent.area is not None else (intent.actor_id, intent.target_id)
    )
    participants = tuple(
        (
            e.id,
            e.status,
            e.spatial_kind,
            p.actor_id,
            p.runtime_position.model_dump(mode="json") if p.runtime_position is not None else None,
            p.facing,
            p.hex_facing,
            p.posture,
        )
        for e in state.encounters
        if e.status == "active"
        for p in e.participants
        if p.actor_id in scoped_actors
    )
    data = {
        "campaign": state.campaign_id,
        "binding": intent.model_dump(mode="json"),
        "locations": [
            (key, entities[key].location_id if key in entities else None) for key in scoped_actors
        ],
        "placements": participants,
        "item": (
            item.owner_id,
            item.ready,
            item.equipped,
            item.container_id,
            item.ground.model_dump(mode="json") if item.ground else None,
            item.world_ground_location_id,
            item.condition.disabled if item.condition else False,
        )
        if item
        else None,
        "hands": item_hands(state, intent.actor_id, intent.item_id),
        "disabled": sorted(disabled(state.resources, intent.actor_id)),
        "conditions": actor.conditions,
        "body": actor.body.model_dump(mode="json") if actor.body else None,
        "grips": [
            g.model_dump(mode="json")
            for e in state.encounters
            if e.status == "active"
            for g in e.grips
            if intent.actor_id in (g.holder_id, g.target_id)
        ],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _invalidate(resources: ResourceState, entry: StaffTouch, revision: int) -> ResourceState:
    if entry.command_id in invalidated(resources):
        return resources
    value = StaffTouchInvalidation(observation_id=entry.command_id, revision=revision)
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=identifier(INVALIDATION_PREFIX, entry.command_id),
                    at=resources.game_time,
                    target_id=entry.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def invalidate_movement(
    resources: ResourceState,
    encounter_id: str,
    actor_ids: frozenset[str],
    *,
    revision: int,
) -> ResourceState:
    """Use only executed movement, including loops and a Wait's executed prefix."""
    bound = {i.cast_id: i for i in intents(resources)}
    for entry in observations(resources):
        intent = bound.get(entry.cast_id)
        affected = (
            {entry.actor_id}
            if intent and intent.area is not None
            else {entry.actor_id, entry.target_id}
        )
        if entry.encounter_id == encounter_id and actor_ids & affected:
            resources = _invalidate(resources, entry, revision)
    return resources


def checkpoint(state: PlayState, *, before: PlayState) -> PlayState:
    resources = state.resources
    bound = {i.cast_id: i for i in intents(resources)}
    current_gms = {m.principal_id for m in state.members if m.role == "gm"}
    for entry in observations(resources):
        if entry.command_id in invalidated(resources):
            continue
        intent = bound.get(entry.cast_id)
        changed = intent is None or entry.observed_by not in current_gms
        if intent is not None:
            changed |= entry.scope_digest != scope_digest(state, intent)
            if entry.declared_revision <= before.revision:
                changed |= entry.scope_digest != scope_digest(before, intent)
        if changed:
            resources = _invalidate(resources, entry, state.revision)
    return (
        state.model_copy(update={"resources": resources}) if resources != state.resources else state
    )
