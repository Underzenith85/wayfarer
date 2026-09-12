"""B484-485 repairs in the campaign CAS, using approved skills and owned supplies."""

import hashlib
from fractions import Fraction
from typing import Literal

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.condition_checks import check_modifiers
from wayfarer.engine.simulation.object_repairs import RepairTask, record, tasks
from wayfarer.engine.simulation.resources import Consume
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def repair(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    item_id: str,
    command_id: str,
    stage: Literal["start", "finish", "cancel"],
    task_id: str | None,
    preview: bool = False,
) -> tuple[PlayState, RepairTask]:
    from wayfarer.engine.simulation.mechanics.gurps_melee import (
        build,
        catalog,
        fatigue_ready,
        level,
    )

    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Repairs require the exact Basic Set profile")
    resources = state.resources
    if stage != "start":
        task = next((t for t in tasks(resources) if t.id == task_id), None)
        if task is None or task.actor_id != actor_id or task.item_id != item_id:
            raise ValidationError("Repair task is not owned by this actor")
        if task.status != "pending":
            raise ConflictError("Repair attempt is already settled")
        if stage == "cancel":
            task = task.model_copy(update={"status": "cancelled"})
            return state.model_copy(update={"resources": record(resources, task, command_id)}), task
    elif task_id is not None:
        raise ValidationError("Starting a repair does not take a previous task ID")
    if any(e.status == "active" and actor_id in e.turn_order for e in state.encounters):
        raise ConflictError("Repairs require half an hour outside active combat")
    item = next((i for i in resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or item.ground or item.equipped:
        raise ValidationError("Repair requires an owned, retrieved, unequipped item")
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    profile = entry.durability
    if not profile or not item.condition or item.condition.destroyed:
        raise ValidationError("Destroyed or unprofiled equipment cannot be repaired")
    hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
    if (
        not hp.injury
        or hp.injury.incapacitated
        or hp.injury.stunned
        or not fatigue_ready(state, actor_id)
    ):
        raise ValidationError("Incapacitated actors cannot repair equipment")
    if stage == "start":
        if any(
            t.status == "pending" and (t.actor_id == actor_id or t.item_id == item_id)
            for t in tasks(resources)
        ):
            raise ConflictError("Finish or cancel the existing repair attempt")
        if item.condition.hp == profile.hp and not item.condition.disabled:
            raise ValidationError("Equipment does not need repair")
        if not profile.repair_skill_id or not profile.repair_tools_definition:
            raise ValidationError("Repairs require pinned skill and equipment bindings")
        tool = next(
            (
                i
                for i in resources.items
                if i.owner_id == actor_id
                and not i.ground
                and i.definition_id == profile.repair_tools_definition
                and (i.condition is None or not i.condition.disabled)
            ),
            None,
        )
        if tool is None:
            raise ValidationError("The required repair equipment is unavailable")
        skill = int(level(build(runtime, state, actor_id), profile.repair_skill_id).value)
        skill += (
            1
            if entry.price <= 1000
            else 0
            if entry.price <= 10000
            else -1
            if entry.price <= 100000
            else -2
            if entry.price <= 1000000
            else -3
        )
        parts_die = None
        quantity = 0
        if item.condition.hp <= 0:
            skill -= 2
            part_entry = next(
                (
                    e
                    for e in catalog(runtime).entries
                    if e.definition_id == profile.repair_parts_definition
                ),
                None,
            )
            supplies = next(
                (
                    i
                    for i in resources.items
                    if i.owner_id == actor_id
                    and not i.ground
                    and not i.equipped
                    and i.definition_id == profile.repair_parts_definition
                ),
                None,
            )
            if part_entry is None or part_entry.price <= 0 or supplies is None:
                raise ValidationError("Major repair requires priced, owned spare parts")
            # Preflight the maximum cost before RNG; insufficient supplies cannot fish for a cheaper roll.
            entry_price = Fraction(entry.price)
            part_price = Fraction(part_entry.price)
            maximum = int((entry_price * 6 + part_price * 10 - 1) // (part_price * 10))
            if supplies.quantity < maximum:
                raise ValidationError(
                    "Major repair requires supplies covering the maximum parts cost"
                )
            parts_die = 6 if preview else draw_dice(runtime.rng, 1)[0]
            quantity = int((entry_price * parts_die + part_price * 10 - 1) // (part_price * 10))
            if quantity:
                resources = runtime.resources.apply(
                    resources,
                    Consume(
                        id="repair-parts:" + hashlib.sha256(command_id.encode()).hexdigest(),
                        actor_id=actor_id,
                        expected_revision=resources.revision,
                        item_id=supplies.id,
                        quantity=quantity,
                    ),
                )
        task = RepairTask(
            id=command_id,
            actor_id=actor_id,
            item_id=item_id,
            tool_id=tool.id,
            start=resources.game_time,
            due=resources.game_time + 1800,
            skill=skill,
            condition=item.condition,
            parts_die=parts_die,
            parts_quantity=quantity,
        )
    else:
        assert task_id is not None
        task = next(t for t in tasks(resources) if t.id == task_id)
        if resources.game_time < task.due:
            raise ConflictError("Repair work has not reached its shared-clock deadline")
        if item.condition != task.condition or not any(
            i.id == task.tool_id
            and i.owner_id == actor_id
            and not i.ground
            and (i.condition is None or not i.condition.disabled)
            for i in resources.items
        ):
            raise ConflictError("Repair equipment changed; cancel this attempt")
        if preview:
            return state, task
        check = success_roll(
            profile.profile_id,
            task.skill,
            check_modifiers(resources, actor_id, "iq"),
            rng=runtime.rng,
        )
        restored = (
            min(profile.hp - item.condition.hp, max(1, check.margin))
            if check.outcome.succeeded
            else 0
        )
        condition = item.condition.model_copy(
            update={
                "hp": item.condition.hp + restored,
                "disabled": False if check.outcome.succeeded else item.condition.disabled,
                "residual_roll": None if check.outcome.succeeded else item.condition.residual_roll,
                "shock": 0,
                "shock_until": None,
            }
        )
        resources = resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"condition": condition}) if i.id == item_id else i
                    for i in resources.items
                )
            }
        )
        task = task.model_copy(
            update={"status": "completed", "check": check, "restored_hp": restored}
        )
    resources = record(resources, task, command_id)
    return state.model_copy(update={"resources": resources}), task
