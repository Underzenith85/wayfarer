"""B484-485 repairs in the campaign CAS, using approved skills and owned supplies."""

from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.skills.mundane.arts import OBJECT_REPAIR_SKILLS, PROCEDURES
from wayfarer.engine.rules.skills.technology_level import technology_level_penalty
from wayfarer.engine.rules.types.skill import ControllingAttribute
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, fatigue_ready, level
from wayfarer.engine.simulation.equipment import repair_time
from wayfarer.engine.simulation.equipment.armoury_context import repair_familiarity_modifier
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, MeleeMode, RangedMode
from wayfarer.engine.simulation.equipment.repair_parts_supply import (
    major_parts,
    revalidate_assessed_parts,
)
from wayfarer.engine.simulation.equipment.repairs import RepairTask, record, tasks
from wayfarer.engine.simulation.equipment.worksite import available_here
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def _repair_binding(entry: EquipmentProfile, skill_id: str) -> tuple[str | None, str | None]:
    """Bind supported Armoury tasks only to matching equipment specialties."""
    if skill_id not in OBJECT_REPAIR_SKILLS:
        return None, None
    procedure = PROCEDURES[skill_id]
    assert procedure.task is not None
    matches = (
        entry.armor is not None
        if skill_id == "skill:armoury-body-armor"
        else entry.shield is not None
        or any(
            isinstance(mode, MeleeMode)
            or isinstance(mode, RangedMode)
            and mode.thrown
            and mode.skill_id.startswith("skill:thrown-weapon-")
            for mode in entry.modes
        )
    )
    if not matches:
        raise ValidationError("Armoury specialty does not match the equipment")
    return procedure.id, procedure.task.effect


def _armoury_tl_penalty(skill_tl: int, equipment_tl: int) -> int:
    """Characters third printing B168: IQ-based technological skill table."""
    return technology_level_penalty(skill_tl, equipment_tl, ControllingAttribute.IQ)


def _repair_skill(
    compiled: ValidatedBuild, skill_id: str, entry: EquipmentProfile, source_bound: bool
) -> tuple[int, int | None, int | None, int]:
    skill = int(level(compiled, skill_id).value)
    if not source_bound:
        return skill, None, None, 0
    purchase = next((p for p in compiled.purchases if p.definition_id == skill_id), None)
    if purchase is None or purchase.technology_level is None:
        raise ValidationError("Armoury restoration requires an approved skill TL purchase")
    if not isinstance(entry.technology_level, int):
        raise ValidationError("Armoury restoration requires a concrete equipment TL")
    penalty = _armoury_tl_penalty(purchase.technology_level, entry.technology_level)
    return skill + penalty, purchase.technology_level, entry.technology_level, penalty


def _tool_modifier(entry: EquipmentProfile, skill_id: str) -> int:
    """Consume the pinned toolkit's exact authored skill modifier (B345)."""
    features = tuple(f for f in entry.general if f.kind == "tool" and f.skill_id == skill_id)
    if len(features) > 1:
        raise ValidationError("Repair toolkit has ambiguous modifiers for this skill")
    if not features:
        return 0
    feature = features[0]
    if feature.consumable_definition_id is not None or feature.duration_seconds is not None:
        raise ValidationError("Repair toolkit consumption or operating limits are unsupported")
    return feature.modifier


def _repair_difficulty(entry: EquipmentProfile, hp: int) -> int:
    """B484–485 price bands and additional major-repair difficulty."""
    price_modifier = (
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
    return price_modifier - (2 if hp <= 0 else 0)


def _repair_modifiers(state: PlayState, actor_id: str, skill: int) -> tuple[Modifier, ...]:
    """B345: a nondefense success roll requires effective skill of at least three."""
    modifiers = check_modifiers(state.resources, actor_id, "iq")
    if skill + sum(modifier.value for modifier in modifiers) < 3:
        raise ValidationError("Repair effective skill must be at least 3")
    return modifiers


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
    assessment_only: bool = False,
    selection_modifier: int = 0,
) -> tuple[PlayState, RepairTask]:
    if (assessment_only or selection_modifier) and (stage != "start" or not preview):
        raise ValidationError("Parts assessment requires a pure start eligibility check")
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
    if (
        item is None
        or item.owner_id != actor_id
        or item.ground
        or item.equipped
        or not available_here(state, actor_id, item)
    ):
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
        procedure_id, effect = _repair_binding(entry, profile.repair_skill_id)
        tool = next(
            (
                i
                for i in resources.items
                if i.owner_id == actor_id
                and not i.ground
                and available_here(state, actor_id, i)
                and i.definition_id == profile.repair_tools_definition
                and (i.condition is None or not i.condition.disabled)
            ),
            None,
        )
        if tool is None:
            raise ValidationError("The required repair equipment is unavailable")
        skill, skill_tl, equipment_tl, tl_penalty = _repair_skill(
            build(runtime, state, actor_id),
            profile.repair_skill_id,
            entry,
            procedure_id is not None,
        )
        tool_entry = next(
            e for e in catalog(runtime).entries if e.definition_id == tool.definition_id
        )
        skill += _repair_difficulty(entry, item.condition.hp) + _tool_modifier(
            tool_entry, profile.repair_skill_id
        )
        skill += repair_familiarity_modifier(
            state, actor_id, item.definition_id, profile.repair_skill_id, procedure_id
        )
        time_plan = repair_time.start_plan(
            resources, actor_id, item, entry, command_id, procedure_id, assessment_only
        )
        skill += repair_time.modifier(time_plan)
        _repair_modifiers(state, actor_id, skill + selection_modifier)
        parts_die = None
        quantity = 0
        if item.condition.hp <= 0:
            resources, parts_die, quantity = major_parts(
                runtime,
                state,
                actor_id=actor_id,
                item=item,
                entry=entry,
                command_id=command_id,
                preview=preview,
                assessment_only=assessment_only,
            )
        task = RepairTask(
            id=command_id,
            actor_id=actor_id,
            item_id=item_id,
            tool_id=tool.id,
            start=resources.game_time,
            due=resources.game_time + repair_time.duration(time_plan),
            time_plan=time_plan,
            skill=skill,
            condition=item.condition,
            parts_die=parts_die,
            parts_quantity=quantity,
            procedure_id=procedure_id,
            effect=effect,
            skill_technology_level=skill_tl,
            equipment_technology_level=equipment_tl,
            technology_level_penalty=tl_penalty,
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
            and available_here(state, actor_id, i)
            and (i.condition is None or not i.condition.disabled)
            for i in resources.items
        ):
            raise ConflictError("Repair equipment changed; cancel this attempt")
        revalidate_assessed_parts(
            runtime, state, actor_id=actor_id, item=item, entry=entry, parts_die=task.parts_die
        )
        repair_time.finish_plan(task.time_plan, actor_id, item, entry)
        modifiers = _repair_modifiers(state, actor_id, task.skill)
        if preview:
            return state, task
        check = success_roll(
            profile.profile_id,
            task.skill,
            modifiers,
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
                # B483 reduction applies only below one-third HP. A successful
                # B484 repair can end that state; retaining its selected mode
                # would make an otherwise legal repair fail validation.
                "reduced_definition_id": None
                if check.outcome.succeeded and (item.condition.hp + restored) * 3 >= profile.hp
                else item.condition.reduced_definition_id,
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
    if assessment_only:
        return state, task
    resources = record(resources, task, command_id)
    return state.model_copy(update={"resources": resources}), task
