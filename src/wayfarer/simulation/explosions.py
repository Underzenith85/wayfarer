"""Durable blast causes and shared-clock deadlines; no roll on projection/replay."""

import hashlib

from wayfarer.errors import ConflictError
from wayfarer.models import Record
from wayfarer.rules.explosion_types import ExplosionSpec
from wayfarer.rules.object_types import GroundPosition
from wayfarer.simulation.resources import ResourceEvent, ResourceState


class BlastRecord(Record):
    id: str
    encounter_id: str
    source_item_id: str
    payload: ExplosionSpec
    due: int
    center: GroundPosition | None
    direct_actor_id: str | None = None
    critical: int = 0
    follow_item: bool = False
    resolved: bool = False
    fuse_dice: tuple[int, ...] = ()
    deferred_ticks: int = 0
    evidence: str = ""


def blasts(state: ResourceState) -> tuple[BlastRecord, ...]:
    values: dict[str, BlastRecord] = {}
    for event in state.events:
        if event.id.startswith("weapon-blast:"):
            value = BlastRecord.model_validate_json(event.kind)
            values[value.id] = value
    return tuple(values.values())


def save(state: ResourceState, record: BlastRecord, cause_id: str) -> ResourceState:
    event = ResourceEvent(
        id="weapon-blast:" + hashlib.sha256(cause_id.encode()).hexdigest(),
        at=state.game_time,
        target_id=record.source_item_id,
        kind=record.model_dump_json(),
    )
    prior = next((e for e in state.events if e.id == event.id), None)
    if prior is not None:
        if prior != event:
            raise ConflictError("Blast evidence cannot be replaced")
        return state
    return state.model_copy(update={"events": state.events + (event,)})


def guard(state: ResourceState, *, advance_to: int | None = None) -> None:
    if any(
        not b.resolved
        and (b.due < advance_to if advance_to is not None else b.due <= state.game_time)
        for b in blasts(state)
    ):
        raise ConflictError("Resolve the due weapon explosion before further activity")


def defer_round(
    state: ResourceState, encounter_id: str, ticks: int, command_id: str
) -> tuple[ResourceState, int]:
    pending = next(
        (
            b
            for b in blasts(state)
            if not b.resolved and b.encounter_id == encounter_id and b.due <= state.game_time
        ),
        None,
    )
    if pending is None or not ticks:
        return state, ticks
    return save(
        state,
        pending.model_copy(update={"deferred_ticks": pending.deferred_ticks + ticks}),
        command_id + ":defer-blast",
    ), 0
