"""Private B394/B552 random-strike intent and replayable hit-location result.

The public v1 grapple-location vocabulary cannot represent random strikes. Keep
this extra intent in a typed private event, leaving both public records intact.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.models import Id, Record

PREFIX = "unarmed-random:"


class RandomStrike(Record):
    kind: Literal["unarmed-random-strike"] = "unarmed-random-strike"
    command_id: Id
    encounter_id: Id
    actor_id: Id
    target_id: Id
    pending_id: Id
    resolved_location: HumanLocation | None = None
    location_dice: tuple[int, ...] = Field(default=(), max_length=4)


def pending_id(command_id: str) -> str:
    return "unarmed:" + hashlib.sha256(command_id.encode()).hexdigest()


def event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def random_strike(
    state: PlayState, encounter_id: str, attack_id: str, actor_id: str, target_id: str
) -> RandomStrike | None:
    for event in state.resources.events:
        if event.id.startswith(PREFIX):
            record = RandomStrike.model_validate_json(event.kind)
            if (
                record.pending_id == attack_id
                and record.encounter_id == encounter_id
                and record.actor_id == actor_id
                and record.target_id == target_id
                and event.id == event_id(record.command_id)
                and event.target_id == actor_id
            ):
                return record
    return None


def save(state: PlayState, record: RandomStrike) -> PlayState:
    identifier = event_id(record.command_id)
    event = ResourceEvent(
        id=identifier,
        at=state.resources.game_time,
        target_id=record.actor_id,
        kind=record.model_dump_json(),
    )
    events = tuple(e for e in state.resources.events if e.id != identifier) + (event,)
    return state.model_copy(
        update={"resources": state.resources.model_copy(update={"events": events})}
    )
