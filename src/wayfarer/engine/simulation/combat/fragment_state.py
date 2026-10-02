"""A blast's durable current actor, kept when optional secret Luck is cancelled."""

import hashlib
import json

from wayfarer.engine.simulation.combat.blast_phases import FragmentContinuation
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError

PREFIX = "opponent-fragment:"


def fragment_attack_id(blast_id: str, actor_id: str) -> str:
    return (
        "fragment-attack:" + hashlib.sha256(json.dumps([blast_id, actor_id]).encode()).hexdigest()
    )


def continuations(state: ResourceState) -> tuple[FragmentContinuation, ...]:
    records: dict[str, FragmentContinuation] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            value = FragmentContinuation.model_validate_json(event.kind)
            records[value.resolution.blast_id] = value
    return tuple(records.values())


def active_fragment(state: ResourceState, blast_id: str) -> FragmentContinuation | None:
    return next(
        (
            record
            for record in continuations(state)
            if record.resolution.blast_id == blast_id and record.active
        ),
        None,
    )


def save_fragment(
    state: ResourceState, record: FragmentContinuation, cause_id: str
) -> ResourceState:
    event = ResourceEvent(
        id=PREFIX + hashlib.sha256(cause_id.encode()).hexdigest(),
        at=state.game_time,
        target_id=record.resolution.blast_id,
        kind=record.model_dump_json(),
    )
    prior = next((row for row in state.events if row.id == event.id), None)
    if prior is not None:
        if prior != event:
            raise ConflictError("Fragment continuation identity changed")
        return state
    return state.model_copy(update={"events": state.events + (event,)})
