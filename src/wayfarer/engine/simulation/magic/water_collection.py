"""Measured whole-vessel collection into a separate empty physical container.

Mundane pour duration is scenario measurement, never a B253 invented rate.
"""

import hashlib
from dataclasses import replace
from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.spell_state import latest as spell_states
from wayfarer.engine.simulation.magic.water_parcels import (
    MATERIAL,
    PLACEMENT,
    Parcel,
    material,
    number,
    observations,
    pours,
    require_acyclic,
)
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest, save
from wayfarer.engine.simulation.resources import Advance, Command, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "water-collection:"
SOURCE = ("stream_width_mm", "collection_seconds", "collection_path_clear")
TARGET = ("opening_mm", "capacity_gallons", "condition", "interior_cleanliness")


class Collection(Record):
    id: Id
    actor_id: Id
    source_id: Id
    assembly_id: Id
    container_id: Id
    fact_ids: tuple[Id, ...] = Field(min_length=1)


class DeclareWaterCollection(Command):
    kind: Literal["declare-water-collection"] = "declare-water-collection"
    collection: Collection


class CollectWater(Command):
    kind: Literal["collect-water"] = "collect-water"
    collection_id: Id


class CollectionObservation(Record):
    command_id: Id
    collection: Collection
    source: WaterBody
    receiver: WaterBody
    entities: tuple[Entity, ...]
    facts: tuple[Fact, ...]
    parcels: tuple[Parcel, ...]
    seconds: int = Field(gt=0)


class CollectionReceipt(Record):
    command_id: Id
    outcome: Literal["collected"] = "collected"
    collection_id: Id
    actor_id: Id
    source_id: Id
    assembly_id: Id
    container_id: Id
    gallons: int = Field(gt=0)
    source_remaining_gallons: int = Field(ge=0)
    assembly_gallons: int = Field(ge=0)
    assembly_pure_gallons: int = Field(ge=0)
    container_gallons: int = Field(gt=0)
    elapsed_seconds: int = Field(gt=0)


def event_id(label: str, command_id: str) -> str:
    return PREFIX + label + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def append(
    state: ResourceState, label: str, value: CollectionObservation | CollectionReceipt, actor: str
) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id(label, value.command_id),
                    at=state.game_time,
                    target_id=actor,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def observed(state: ResourceState, identifier: str) -> CollectionObservation:
    values = tuple(
        CollectionObservation.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "observed:")
    )
    found = tuple(o for o in values if o.collection.id == identifier)
    if len(found) != 1:
        raise ValidationError("Water collection requires one immutable authenticated observation")
    return found[0]


def _assembly(state: ResourceState, collection: Collection) -> tuple[str, ...]:
    history = tuple(o for o in observations(state) if o.flow.source_id == collection.assembly_id)
    if not history:
        raise ValidationError("Water collection requires an observed separate-container assembly")
    ended = {p.flow_id for p in pours(state)}
    if any(o.flow.id not in ended for o in history):
        raise ValidationError("An active water parcel observation cannot be refilled")
    containers = history[-1].flow.container_ids
    if collection.container_id not in containers:
        raise ValidationError("Water collection target is not an observed assembly child")
    forbidden = {
        i
        for o in observations(state)
        for i in (o.flow.source_id, o.flow.ring_id, *o.flow.container_ids)
    }
    if collection.source_id in forbidden:
        raise ValidationError("Water collection source requires its own concrete material adapter")
    return containers


def _entities(
    world: World, state: ResourceState, collection: Collection, children: tuple[str, ...]
) -> tuple[Entity, ...]:
    ids = (collection.actor_id, collection.source_id, collection.assembly_id, *children)
    if len(set(ids)) != len(ids) or not set(ids) <= {e.id for e in world.entities}:
        raise ValidationError("Water collection requires distinct current physical objects")
    if set(ids[1:]) & {i.id for i in (*state.items, *state.expended_items)}:
        raise ValidationError("Inventory water requires its concrete item adapter")
    indexed = {e.id: e for e in world.entities}
    require_acyclic(indexed, ids)
    result = tuple(indexed[i] for i in ids)
    actor, source, assembly, *containers = result
    if actor.kind != EntityKind.ACTOR or any(e.kind != EntityKind.OBJECT for e in result[1:]):
        raise ValidationError("Water collection is limited to placed nonliving objects")
    if (
        source.owner_id != actor.id
        or assembly.owner_id != actor.id
        or any(e.owner_id != assembly.id for e in containers)
    ):
        raise ValidationError("Water collection requires current caster custody")
    if any(e.location_id != actor.location_id for e in result[1:]):
        raise ValidationError("Water collection requires current co-location")
    if (
        {e.id for e in world.entities if e.owner_id == assembly.id} != set(children)
        or any(e.owner_id in children for e in world.entities)
        or any(e.owner_id == source.id for e in world.entities)
    ):
        raise ValidationError("Water collection requires complete unnested physical vessels")
    return result


def _facts(world: World, collection: Collection, children: tuple[str, ...]) -> tuple[Fact, ...]:
    expected = {
        (i, p) for i in (collection.source_id, collection.assembly_id, *children) for p in PLACEMENT
    }
    expected |= {(collection.source_id, p) for p in SOURCE}
    expected |= {(collection.container_id, p) for p in TARGET}
    expected |= {(i, p) for i in children for p in MATERIAL}
    if any(f.subject_id == collection.source_id and f.predicate in MATERIAL for f in world.facts):
        expected |= {(collection.source_id, p) for p in MATERIAL}
    found = tuple(f for f in world.facts if f.id in collection.fact_ids)
    if (
        len(set(collection.fact_ids)) != len(collection.fact_ids)
        or len(found) != len(collection.fact_ids)
        or {(f.subject_id, f.predicate) for f in found} != expected
        or len(found) != len(expected)
        or sum((f.subject_id, f.predicate) in expected for f in world.facts) != len(expected)
    ):
        raise ValidationError(
            "Water collection requires exact unique existing physical measurements"
        )
    return tuple(sorted(found, key=lambda f: f.id))


def _physical(
    values: dict[tuple[str, str], str],
    collection: Collection,
    children: tuple[str, ...],
    source: WaterBody,
    receiver: WaterBody,
) -> int:
    for i in (collection.source_id, collection.assembly_id, *children):
        point = tuple(number(values, i, p) for p in PLACEMENT[:2])
        if point != source.position or point != receiver.position:
            raise ValidationError(
                "Water collection placement differs from its measured full-catch path"
            )
    source_height = number(values, collection.source_id, "height_mm")
    receiver_height = number(values, collection.assembly_id, "height_mm")
    if not source_height > receiver_height >= 0 or any(
        number(values, i, "height_mm") != receiver_height for i in children
    ):
        raise ValidationError("Water collection requires descending source and receiving heights")
    width = number(values, collection.source_id, "stream_width_mm")
    opening = number(values, collection.container_id, "opening_mm")
    seconds = number(values, collection.source_id, "collection_seconds")
    capacity = number(values, collection.container_id, "capacity_gallons")
    if (
        not 0 < width <= opening
        or seconds <= 0
        or capacity < source.gallons
        or values[(collection.source_id, "collection_path_clear")] != "true"
        or values[(collection.container_id, "condition")] != "intact"
        or values[(collection.container_id, "interior_cleanliness")] != "clean"
    ):
        raise ValidationError("Water collection requires a complete clean measured receiving path")
    return seconds


def observe(
    world: World, state: ResourceState, collection: Collection, command_id: str
) -> CollectionObservation:
    children = _assembly(state, collection)
    entities = _entities(world, state, collection, children)
    bodies = latest(state)
    source = bodies.get(collection.source_id)
    receiver = bodies.get(collection.assembly_id)
    if (
        source is None
        or receiver is None
        or source.form != "liquid"
        or receiver.form != "liquid"
        or source.gallons <= 0
        or source.pure_gallons not in (0, source.gallons)
        or source.capacity_gallons is None
        or source.location_id != entities[0].location_id
        or receiver.location_id != entities[0].location_id
    ):
        raise ValidationError("Water collection requires a whole current homogeneous-water vessel")
    if (
        receiver.capacity_gallons is None
        or receiver.gallons + source.gallons > receiver.capacity_gallons
    ):
        raise ValidationError("Water collection assembly lacks receiving capacity")
    facts = _facts(world, collection, children)
    values = {(f.subject_id, f.predicate): f.value for f in facts}
    if (collection.source_id, "water_volume_gallons") in values and (
        number(values, collection.source_id, "water_volume_gallons") != source.gallons
        or values[(collection.source_id, "water_purity")]
        != ("pure" if source.pure_gallons else "impure")
    ):
        raise ValidationError("Water collection source measurements contradict current material")
    if source.pure_gallons == 0 and (collection.source_id, "water_purity") not in values:
        raise ValidationError("Impure collection requires measured homogeneous source purity")
    # Reuse the existing complete-container composition contract, not a mixed constituent split.
    prior = next(
        o for o in reversed(observations(state)) if o.flow.source_id == collection.assembly_id
    )
    parcels = material(values, prior.flow, receiver)
    target = next(p for p in parcels if p.object_id == collection.container_id)
    if target.gallons != 0 or collection.container_id in bodies:
        raise ValidationError("Water collection requires one empty non-aliased separate container")
    return CollectionObservation(
        command_id=command_id,
        collection=collection,
        source=source,
        receiver=receiver,
        entities=entities,
        facts=facts,
        parcels=parcels,
        seconds=_physical(values, collection, children, source, receiver),
    )


def declare(state: PlayState, command: DeclareWaterCollection) -> ResourceState:
    if any(
        e.id.startswith(PREFIX + "observed:")
        and CollectionObservation.model_validate_json(e.kind).collection.id == command.collection.id
        for e in state.resources.events
    ):
        raise ConflictError("Water collection observations cannot be replaced")
    value = observe(state.world, state.resources, command.collection, command.id)
    return append(state.resources, "observed", value, command.collection.actor_id)


def _ready(runtime: RulesContext, state: PlayState, value: CollectionObservation) -> None:
    actor = value.collection.actor_id
    synchronous(state, actor)
    guard(state, actor, "collect-water")
    runtime.approved_build(state, actor)
    performer = next((a for a in state.actors if a.actor_id == actor), None)
    if (
        performer is None
        or performer.available_at > state.resources.game_time
        or performer.conditions
    ):
        raise ConflictError("Water collection requires an available actor")
    if any(
        e.actor_id == actor and e.phase == "casting" for e in spell_states(state.resources).values()
    ):
        raise ValidationError("Water collection cannot interrupt current spell concentration")
    pool = next((p for p in state.resources.pools if p.id == "hp:" + actor), None)
    if (
        pool is None
        or pool.injury is None
        or pool.injury.incapacitated
        or pool.injury.stunned
        or not fatigue_ready(state, actor)
        or any(e.status == "active" for e in state.encounters)
    ):
        raise ValidationError("Water collection requires a capable caster outside combat")
    if observe(state.world, state.resources, value.collection, value.command_id) != value:
        raise ConflictError("Water collection physical observation is stale")


def collect(
    runtime: RulesContext, state: PlayState, command: CollectWater
) -> tuple[PlayState, CollectionReceipt]:
    value = observed(state.resources, command.collection_id)
    if value.collection.actor_id != command.actor_id:
        raise ValidationError("Water collection belongs to a different caster")
    if any(
        e.id.startswith(PREFIX + "collected:")
        and CollectionReceipt.model_validate_json(e.kind).collection_id == command.collection_id
        for e in state.resources.events
    ):
        raise ConflictError("Water collection is already settled")
    _ready(runtime, state, value)
    advanced = runtime.advance(
        state,
        Advance(
            id=command.id + ":physical-pour",
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            to=state.resources.game_time + value.seconds,
        ),
    )
    advanced_actor = next((a for a in advanced.actors if a.actor_id == command.actor_id), None)
    if (
        advanced_actor is None
        or advanced_actor.available_at > advanced.resources.game_time
        or advanced_actor.conditions
    ):
        raise ConflictError("Water collection actor became unavailable during measured work")
    if advanced.party != state.party:
        raise ConflictError("Water collection cannot settle newly scheduled party activity")
    if advanced.party.groups:
        if (
            len(advanced.party.groups) != 1
            or command.actor_id not in advanced.party.groups[0].actor_ids
        ):
            raise ConflictError("Water collection cannot settle split-party activity")
        group = advanced.party.groups[0]
        advanced = advanced.model_copy(
            update={
                "party": advanced.party.model_copy(
                    update={
                        "groups": (
                            group.model_copy(
                                update={"ready_through": advanced.resources.game_time}
                            ),
                        )
                    }
                )
            }
        )
    advanced = advanced.model_copy(
        update={
            "actors": tuple(
                actor.model_copy(update={"available_at": advanced.resources.game_time})
                if actor.actor_id == command.actor_id
                else actor
                for actor in advanced.actors
            )
        }
    )
    _ready(runtime, advanced, value)
    source = value.source.model_copy(update={"gallons": 0, "pure_gallons": 0})
    receiver = value.receiver.model_copy(
        update={
            "gallons": value.receiver.gallons + value.source.gallons,
            "pure_gallons": value.receiver.pure_gallons + value.source.pure_gallons,
        }
    )
    resources = save(save(advanced.resources, source, command.id), receiver, command.id)
    facts = tuple(
        replace(
            f,
            value=(
                str(value.source.gallons)
                if f.predicate == "water_volume_gallons"
                else ("pure" if value.source.pure_gallons else "impure")
            ),
        )
        if f.subject_id == value.collection.container_id and f.predicate in MATERIAL
        else replace(f, value="0" if f.predicate == "water_volume_gallons" else "empty")
        if f.subject_id == value.collection.source_id and f.predicate in MATERIAL
        else f
        for f in advanced.world.facts
    )
    world = replace(advanced.world, facts=facts)
    current_values = {(f.subject_id, f.predicate): f.value for f in facts}
    prior = next(
        o for o in reversed(observations(resources)) if o.flow.source_id == receiver.object_id
    )
    material(current_values, prior.flow, receiver)
    receipt = CollectionReceipt(
        command_id=command.id,
        collection_id=command.collection_id,
        actor_id=command.actor_id,
        source_id=source.object_id,
        assembly_id=receiver.object_id,
        container_id=value.collection.container_id,
        gallons=value.source.gallons,
        source_remaining_gallons=0,
        assembly_gallons=receiver.gallons,
        assembly_pure_gallons=receiver.pure_gallons,
        container_gallons=value.source.gallons,
        elapsed_seconds=value.seconds,
    )
    resources = append(resources, "collected", receipt, command.actor_id).model_copy(
        update={"revision": state.revision + 1}
    )
    return advanced.model_copy(
        update={"revision": state.revision + 1, "resources": resources, "world": world}
    ), receipt
