"""Permanent B253 mist water, without assuming collection or evaporation."""

import hashlib
from typing import Literal

from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "water-mist:"


class MistMaterial(Record):
    command_id: Id
    actor_id: Id
    scene_id: Id
    carrier_id: Id
    position: tuple[int, int]
    gallons: Literal[1] = 1
    pure: Literal[True] = True


def record(state: ResourceState, material: MistMaterial) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(material.command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=material.carrier_id,
                    kind=material.model_dump_json(),
                ),
            )
        }
    )
