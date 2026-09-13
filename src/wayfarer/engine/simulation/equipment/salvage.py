"""Pinned dismantling and recovered-material custody for damaged artifacts."""

from typing import Literal

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.equipment.salvage_state import SalvageTask, record, tasks
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def _record(state: PlayState, task: SalvageTask, command_id: str) -> PlayState:
    return state.model_copy(update={"resources": record(state.resources, task, command_id)})


def salvage(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    item_id: str,
    command_id: str,
    stage: Literal["start", "finish", "cancel"],
    task_id: str | None,
) -> tuple[PlayState, SalvageTask]:
    task = next((value for value in tasks(state.resources) if value.id == task_id), None)
    if stage != "start":
        if task is None or (task.actor_id, task.item_id) != (actor_id, item_id):
            raise ValidationError("Salvage task is unavailable")
        if task.status != "pending":
            raise ConflictError("Salvage task is already settled")
        if stage == "cancel":
            task = task.model_copy(update={"status": "cancelled"})
            return _record(state, task, command_id), task
    elif task_id is not None or any(
        t.status == "pending" and t.actor_id == actor_id for t in tasks(state.resources)
    ):
        raise ConflictError("Finish or cancel the existing salvage task")
    if any(e.status == "active" and actor_id in e.turn_order for e in state.encounters):
        raise ConflictError("Dismantling requires work outside active combat")
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or item.ground or item.equipped:
        raise ValidationError("Salvage requires an owned, retrieved, unequipped object")
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    profile = entry.durability
    if not profile or not profile.salvage or not item.condition or item.condition.hp >= profile.hp:
        raise ValidationError("Object has no pinned damaged-object salvage procedure")
    spec = profile.salvage
    tool = next(
        (
            i
            for i in state.resources.items
            if i.owner_id == actor_id
            and not i.ground
            and i.definition_id == spec.tools_definition_id
            and (i.condition is None or not i.condition.disabled)
        ),
        None,
    )
    if tool is None:
        raise ValidationError("Required salvage tools are unavailable")
    if stage == "start":
        skill = int(level(build(runtime, state, actor_id), spec.skill_id).value)
        task = SalvageTask(
            id=command_id,
            actor_id=actor_id,
            item_id=item_id,
            tool_id=tool.id,
            definition_id=item.definition_id,
            condition=item.condition,
            start=state.resources.game_time,
            due=state.resources.game_time + spec.seconds,
            skill=skill,
        )
        return _record(state, task, command_id), task
    assert task is not None
    if state.resources.game_time < task.due:
        raise ConflictError("Salvage task has not reached its shared-clock deadline")
    if tool.id != task.tool_id:
        raise ConflictError("Pinned salvage tools changed")
    if item.definition_id != task.definition_id or item.condition != task.condition:
        raise ConflictError("Pinned salvage object facts changed")
    check = success_roll(
        profile.profile_id,
        task.skill,
        check_modifiers(state.resources, actor_id, "iq"),
        rng=runtime.rng,
    )
    quantity = (
        (spec.destroyed_quantity if item.condition.destroyed else spec.disabled_quantity)
        if check.outcome.succeeded
        else 0
    )
    recovered_id = item.id + ":salvage:" + task.id
    recovered = Item(
        id=recovered_id,
        definition_id=spec.recovered_definition_id,
        owner_id=actor_id,
        quantity=quantity or 1,
    )
    items = tuple(i for i in state.resources.items if i.id != item.id)
    if quantity:
        items += (recovered,)
    resources = state.resources.model_copy(update={"items": items})
    runtime.resources.validate(resources)
    state = state.model_copy(update={"resources": resources})
    task = task.model_copy(
        update={
            "status": "completed",
            "check_dice": check.dice,
            "recovered_item_id": recovered_id if quantity else None,
            "recovered_quantity": quantity,
        }
    )
    return _record(state, task, command_id), task
