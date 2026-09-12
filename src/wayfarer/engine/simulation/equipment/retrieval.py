"""Timed recovery of owned ground equipment after an encounter, including off-board landings."""

import hashlib
from typing import Literal

from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import Encounter
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class RetrievalTask(Record):
    id: str
    actor_id: str
    item_id: str
    landing: GroundPosition
    location_id: str
    due: int
    status: Literal["pending", "completed", "cancelled"] = "pending"


def tasks(resources: ResourceState) -> tuple[RetrievalTask, ...]:
    latest: dict[str, RetrievalTask] = {}
    for event in resources.events:
        if event.id.startswith("equipment-retrieval:"):
            task = RetrievalTask.model_validate_json(event.kind)
            latest[task.id] = task
    return tuple(latest.values())


def retrieve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    actor_id: str,
    item_id: str,
    command_id: str,
    stage: Literal["start", "finish", "cancel"],
    task_id: str | None,
) -> tuple[PlayState, RetrievalTask]:
    from wayfarer.engine.simulation.combat.melee import movement

    task = next((t for t in tasks(state.resources) if t.id == task_id), None)
    if stage != "start":
        if task is None or (task.actor_id, task.item_id) != (actor_id, item_id):
            raise ValidationError("Retrieval task is unavailable")
        if task.status != "pending":
            raise ConflictError("Retrieval task is already settled")
    elif task_id is not None or any(
        t.status == "pending" and t.actor_id == actor_id for t in tasks(state.resources)
    ):
        raise ConflictError("Finish or cancel the existing retrieval")
    if stage == "cancel":
        assert task is not None
        task = task.model_copy(update={"status": "cancelled"})
    else:
        item = next((i for i in state.resources.items if i.id == item_id), None)
        if item is None or item.owner_id != actor_id or item.ground is None:
            raise ValidationError("Retrieval requires owned ground equipment")
        if encounter.status != "completed" or item.ground.encounter_id != encounter.id:
            raise ValidationError("Field retrieval requires the completed original encounter")
        if any(e.status == "active" and actor_id in e.turn_order for e in state.encounters):
            raise ConflictError("Finish active combat before field retrieval")
        rules = runtime.rules.combat
        assert rules is not None
        field = next(f for f in rules.battlefields if f.id == encounter.battlefield_id)
        actor = next(e for e in state.world.entities if e.id == actor_id)
        if actor.location_id != field.location_id:
            raise ValidationError("Return to the encounter location before retrieval")
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
        if hp.injury and (hp.injury.incapacitated or hp.injury.stunned):
            raise ValidationError("Incapacitated actors cannot retrieve equipment")
        if stage == "start":
            subject = next(p for p in encounter.participants if p.actor_id == actor_id)
            if isinstance(subject.position, Hex):
                dq, dr = subject.position.q - item.ground.x, subject.position.r - item.ground.y
                distance = max(abs(dq), abs(dr), abs(dq + dr))
            else:
                distance = max(
                    abs(subject.position.x - item.ground.x), abs(subject.position.y - item.ground.y)
                )
            speed = movement(runtime, state, actor_id)
            if speed <= 0:
                raise ValidationError("Retrieval requires mobility")
            # Walk out, pick up, and return. Shared time is advanced by the party clock.
            seconds = 2 * ((distance + speed - 1) // speed) + 1
            task = RetrievalTask(
                id=command_id,
                actor_id=actor_id,
                item_id=item_id,
                landing=item.ground,
                location_id=actor.location_id,
                due=state.resources.game_time + seconds,
            )
        else:
            assert task is not None
            if item.ground != task.landing or actor.location_id != task.location_id:
                raise ConflictError("Retrieval location changed; cancel the attempt")
            if state.resources.game_time < task.due:
                raise ConflictError("Retrieval has not reached its shared-clock deadline")
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(update={"ground": None}) if i.id == item_id else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
            task = task.model_copy(update={"status": "completed"})
    assert task is not None
    resources = state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id="equipment-retrieval:" + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=item_id,
                    kind=task.model_dump_json(),
                ),
            )
        }
    )
    runtime.resources.validate(resources)
    return state.model_copy(update={"resources": resources}), task
