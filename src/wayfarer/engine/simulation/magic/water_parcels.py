"""Private B253 observations of complete, physically separate water containers.

This carrier cannot divide a container or select constituents of a mixture. An
observation authorizes one finite ordinary pour, through a measured actual ring.
"""

import hashlib
from dataclasses import replace
from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "water-parcels:"
PLACEMENT = ("position_x_yards", "position_y_yards", "height_mm")
RING = ("opening_mm", "condition", "stream_width_mm", "flow_seconds_per_gallon")
MATERIAL = ("water_volume_gallons", "water_purity")


class ParcelFlow(Record):
    id: Id
    caster_id: Id
    source_id: Id
    target_id: Id
    ring_id: Id
    container_ids: tuple[Id, ...] = Field(min_length=1)
    fact_ids: tuple[Id, ...] = Field(min_length=1)


class DeclareWaterParcels(Command):
    kind: Literal["declare-water-parcels"] = "declare-water-parcels"
    flow: ParcelFlow


class Parcel(Record):
    object_id: Id
    gallons: int = Field(ge=0)
    pure: bool


class Observation(Record):
    command_id: Id
    flow: ParcelFlow
    source: WaterBody
    entities: tuple[Entity, ...]
    facts: tuple[Fact, ...]
    parcels: tuple[Parcel, ...]
    seconds_per_gallon: int


class Pour(Record):
    command_id: Id
    flow_id: Id
    containers: tuple[Parcel, ...]


def observations(state: ResourceState) -> tuple[Observation, ...]:
    return tuple(
        Observation.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "observed:")
    )


def pours(state: ResourceState) -> tuple[Pour, ...]:
    return tuple(
        Pour.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "poured:")
    )


def observation(state: ResourceState, flow_id: str) -> Observation:
    values = tuple(o for o in observations(state) if o.flow.id == flow_id)
    if len(values) != 1:
        raise ValidationError("Water pour requires one immutable physical observation")
    return values[0]


def number(values: dict[tuple[str, str], str], subject: str, predicate: str) -> int:
    value = values[(subject, predicate)]
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Water measurements require canonical whole units") from exc
    if str(result) != value:
        raise ValidationError("Water measurements require canonical whole units")
    return result


def require_acyclic(indexed: dict[str, Entity], ids: tuple[str, ...]) -> None:
    for object_id in ids:
        seen: set[str] = set()
        current: str | None = object_id
        while current is not None and current in indexed:
            if current in seen:
                raise ValidationError("Water object custody cannot be cyclic")
            seen.add(current)
            current = indexed[current].owner_id


def topology(world: World, resources: ResourceState, flow: ParcelFlow) -> tuple[Entity, ...]:
    ids = (flow.caster_id, flow.source_id, flow.target_id, flow.ring_id, *flow.container_ids)
    if len(set(ids)) != len(ids):
        raise ValidationError("Water objects and separate containers must be distinct")
    indexed = {e.id: e for e in world.entities}
    if not set(ids) <= indexed.keys():
        raise ValidationError("Water physical observation has missing objects")
    require_acyclic(indexed, ids)
    if set(ids[1:]) & {i.id for i in resources.items}:
        raise ValidationError("Inventory-bound water objects require their concrete item adapter")
    entities = tuple(indexed[i] for i in ids)
    actor, source, target, ring, *children = entities
    if actor.kind != EntityKind.ACTOR or any(e.kind != EntityKind.OBJECT for e in entities[1:]):
        raise ValidationError("Water observation requires an actual caster and objects")
    if any(e.owner_id != actor.id for e in (source, target, ring)):
        raise ValidationError("Water source, receiver and ring require current caster custody")
    if any(e.location_id != actor.location_id for e in entities[1:]):
        raise ValidationError("Water pour objects require the caster's current location")
    if any(e.owner_id != source.id for e in children):
        raise ValidationError("Water containers require direct current source custody")
    if {e.id for e in world.entities if e.owner_id == source.id} != set(flow.container_ids):
        raise ValidationError("Water observation must include the complete child container set")
    if any(e.owner_id in flow.container_ids for e in world.entities):
        raise ValidationError("Nested water containers are unsupported")
    if set(flow.container_ids) & latest(resources).keys() or ring.id in latest(resources):
        raise ValidationError("Separate water containers cannot duplicate aggregate water bodies")
    return entities


def measurements(world: World, flow: ParcelFlow) -> tuple[Fact, ...]:
    expected = {
        (i, p)
        for i in (flow.source_id, flow.target_id, flow.ring_id, *flow.container_ids)
        for p in PLACEMENT
    }
    expected |= {(flow.ring_id, p) for p in RING}
    expected |= {(i, p) for i in flow.container_ids for p in MATERIAL}
    selected = tuple(f for f in world.facts if f.id in flow.fact_ids)
    if len(set(flow.fact_ids)) != len(flow.fact_ids) or len(selected) != len(flow.fact_ids):
        raise ValidationError("Water observation requires unique existing measurement references")
    if {(f.subject_id, f.predicate) for f in selected} != expected or len(selected) != len(
        expected
    ):
        raise ValidationError("Water observation requires exact measured units and material facts")
    if sum((f.subject_id, f.predicate) in expected for f in world.facts) != len(expected):
        raise ValidationError("Water physical measurement is ambiguous")
    return tuple(sorted(selected, key=lambda f: f.id))


def geometry(
    values: dict[tuple[str, str], str], flow: ParcelFlow, bodies: dict[str, WaterBody]
) -> int:
    point = bodies[flow.source_id].position
    for i in (flow.source_id, flow.target_id, flow.ring_id, *flow.container_ids):
        measured = tuple(number(values, i, p) for p in PLACEMENT[:2])
        if measured != point:
            raise ValidationError("Water pour requires measured co-located placement")
    if bodies[flow.target_id].position != point:
        raise ValidationError("Water receiver differs from measured placement")
    heights = tuple(
        number(values, i, "height_mm") for i in (flow.source_id, flow.ring_id, flow.target_id)
    )
    if not heights[0] > heights[1] > heights[2] >= 0:
        raise ValidationError("Water pour requires descending source, ring and receiver heights")
    if any(number(values, i, "height_mm") != heights[0] for i in flow.container_ids):
        raise ValidationError("Separate containers differ from the measured source position")
    opening = number(values, flow.ring_id, "opening_mm")
    width = number(values, flow.ring_id, "stream_width_mm")
    rate = number(values, flow.ring_id, "flow_seconds_per_gallon")
    if (
        not 0 < width <= opening
        or values[(flow.ring_id, "condition")] != "intact"
        or not 5 <= rate <= 10
    ):
        raise ValidationError("Water requires an intact fitting ring and ordinary measured flow")
    return rate


def material(
    values: dict[tuple[str, str], str], flow: ParcelFlow, source: WaterBody
) -> tuple[Parcel, ...]:
    result = []
    for i in flow.container_ids:
        gallons = number(values, i, "water_volume_gallons")
        purity = values[(i, "water_purity")]
        if gallons < 0 or purity not in (("pure", "impure") if gallons else ("empty",)):
            raise ValidationError("Each separate water container requires exact complete material")
        result.append(Parcel(object_id=i, gallons=gallons, pure=purity == "pure"))
    if (
        sum(p.gallons for p in result) != source.gallons
        or sum(p.gallons for p in result if p.pure) != source.pure_gallons
    ):
        raise ValidationError("Water container composition must conserve the complete aggregate")
    return tuple(result)


def observe(world: World, state: ResourceState, flow: ParcelFlow, command_id: str) -> Observation:
    entities = topology(world, state, flow)
    bodies = latest(state)
    if not {flow.source_id, flow.target_id} <= bodies.keys():
        raise ValidationError("Water physical observation requires current aggregate and receiver")
    source = bodies[flow.source_id]
    if source.form != "liquid" or source.location_id != entities[0].location_id:
        raise ValidationError("Water physical source must be current placed liquid")
    facts = measurements(world, flow)
    values = {(f.subject_id, f.predicate): f.value for f in facts}
    return Observation(
        command_id=command_id,
        flow=flow,
        source=source,
        entities=entities,
        facts=facts,
        parcels=material(values, flow, source),
        seconds_per_gallon=geometry(values, flow, bodies),
    )


def append(
    state: ResourceState, label: str, record: Observation | Pour, actor_id: str
) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX
                    + label
                    + ":"
                    + hashlib.sha256(record.command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=actor_id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )


def declare(world: World, state: ResourceState, command: DeclareWaterParcels) -> ResourceState:
    if any(o.flow.id == command.flow.id for o in observations(state)):
        raise ConflictError("Water physical observations cannot be replaced")
    return append(
        state, "observed", observe(world, state, command.flow, command.id), command.flow.caster_id
    )


def require_current(world: World, state: ResourceState, flow_id: str | None, actor_id: str) -> None:
    if flow_id is None:
        return
    stored = observation(state, flow_id)
    if any(p.flow_id == flow_id for p in pours(state)):
        raise ValidationError("Water observed finite pour already ended")
    if stored.flow.caster_id != actor_id:
        raise ValidationError("Water observation belongs to a different caster")
    if observe(world, state, stored.flow, stored.command_id) != stored:
        raise ValidationError("Water physical observation is stale")


def selected(
    state: ResourceState,
    flow_id: str | None,
    source_id: str | None,
    target_id: str,
    gallons: int,
    rate: int,
) -> tuple[Parcel, ...]:
    if flow_id is None:
        return ()
    stored = observation(state, flow_id)
    if any(p.flow_id == flow_id for p in pours(state)):
        raise ValidationError("Water observed finite pour already ended")
    if (source_id, target_id, rate) != (
        stored.flow.source_id,
        stored.flow.target_id,
        stored.seconds_per_gallon,
    ) or latest(state).get(source_id or "") != stored.source:
        raise ValidationError("Water plan differs from its current measured source flow")
    result: list[Parcel] = []
    total = 0
    for parcel in stored.parcels:
        if total == gallons:
            break
        total += parcel.gallons
        result.append(parcel)
    if total != gallons:
        raise ValidationError("Water pour cannot cut inside a complete separate container")
    return tuple(result)


def drain(
    state: ResourceState,
    flow_id: str | None,
    source: WaterBody,
    target_id: str,
    gallons: int,
    rate: int,
    command_id: str,
) -> tuple[ResourceState, WaterBody | None]:
    if flow_id is None:
        return state, None
    containers = selected(state, flow_id, source.object_id, target_id, gallons, rate)
    updated = source.model_copy(
        update={
            "gallons": source.gallons - gallons,
            "pure_gallons": source.pure_gallons - sum(p.gallons for p in containers if p.pure),
        }
    )
    return append(
        state,
        "poured",
        Pour(command_id=command_id, flow_id=flow_id, containers=containers),
        observation(state, flow_id).flow.caster_id,
    ), updated


def settle(
    world: World, before: ResourceState, after: ResourceState, flow_id: str | None, command_id: str
) -> World:
    if flow_id is None:
        return world
    stored = observation(before, flow_id)
    require_current(world, before, flow_id, stored.flow.caster_id)
    poured = tuple(p for p in pours(after) if p.command_id == command_id and p.flow_id == flow_id)
    if len(poured) != 1:
        raise ValidationError("Water material settlement requires its accepted pour")
    expected = selected(
        before,
        flow_id,
        stored.flow.source_id,
        stored.flow.target_id,
        sum(p.gallons for p in poured[0].containers),
        stored.seconds_per_gallon,
    )
    if poured[0].containers != expected:
        raise ValidationError("Water accepted pour differs from its measured complete prefix")
    ids = {p.object_id for p in poured[0].containers}
    facts = tuple(
        replace(f, value="0" if f.predicate == "water_volume_gallons" else "empty")
        if f.subject_id in ids and f.predicate in MATERIAL
        else f
        for f in world.facts
    )
    updated = replace(world, facts=facts)
    observe(updated, after, stored.flow, stored.command_id)
    return updated


def reject_aggregate_alias(state: ResourceState, object_id: str) -> None:
    if any(
        object_id in o.flow.container_ids or object_id == o.flow.ring_id
        for o in observations(state)
    ):
        raise ValidationError("Observed separate containers cannot duplicate water aggregates")


def protect_material(
    state: ResourceState, flow_id: str | None, body_ids: tuple[str | None, ...]
) -> None:
    assemblies = {o.flow.source_id for o in observations(state)}
    permitted = {observation(state, flow_id).flow.source_id} if flow_id is not None else set()
    if assemblies.intersection(body_ids) - permitted:
        raise ValidationError(
            "Observed separate-container material requires its concrete pour adapter"
        )
