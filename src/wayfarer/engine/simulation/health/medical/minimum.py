"""B345 admission for voluntary care, captured independently per treatment."""

import hashlib

from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.rules.types import care_minimum
from wayfarer.engine.rules.types.care_minimum import PREFIX as PREFIX
from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError


def require(state: ResourceState, actor_id: str, skill: int, modifier: int = 0) -> None:
    if skill + modifier + sum(m.value for m in check_modifiers(state, actor_id, "iq")) < 3:
        raise ValidationError("Medical effective skill must be at least 3")


def check_inputs(state: ResourceState, task: RecoveryTask) -> tuple[int, tuple[Modifier, ...]]:
    if task.skill is None:
        raise ValidationError("Treatment requires a compiled medical skill")
    adjustment = task.treatment_modifier if task.kind in {"resuscitate", "stabilize"} else 0
    return task.skill + adjustment, check_modifiers(state, task.actor_id, "iq")


def record(state: ResourceState, task: RecoveryTask, *, enabled: bool) -> ResourceState:
    if not enabled:
        return state
    value = care_minimum.producer(task)
    target, modifiers = check_inputs(state, task)
    if target + sum(m.value for m in modifiers) < 3:
        raise ValidationError("Medical effective skill must be at least 3")
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(task.id.encode()).hexdigest(),
                    at=task.start,
                    target_id=task.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def enabled(state: ResourceState, task: RecoveryTask) -> bool:
    return care_minimum.enabled(task, state.events, state.game_time)


def interrupt_unavailable(state: ResourceState, task: RecoveryTask) -> RecoveryTask:
    if task.status != "pending" or state.game_time < task.due or not enabled(state, task):
        return task
    target, modifiers = check_inputs(state, task)
    if target + sum(m.value for m in modifiers) >= 3:
        return task
    return task.model_copy(update={"status": "interrupted", "interrupted_at": state.game_time})
