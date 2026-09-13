"""Restart-safe ordinary invention transitions over canonical project/resource state."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.gadgeteering import capability as gadgeteering_capability
from wayfarer.engine.character.traits.gadgeteering import permits as permits_gadgeteering
from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import level
from wayfarer.engine.simulation.equipment.repairs import tasks as repair_tasks
from wayfarer.engine.simulation.projects.inventions import (
    GadgetDefect,
    GadgetDefectKind,
    GadgetOperation,
    InventionAttempt,
    InventionBlueprint,
    InventionPhase,
    InventionProject,
    InventionRules,
    InventionWork,
    MaterialRequirement,
    ProductionLot,
    ScheduleKind,
    StageRequirement,
    busy_actor_ids,
    phase_after,
)
from wayfarer.engine.simulation.resources import Item, Receipt, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class InventionCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class CreateInvention(InventionCommand):
    kind: Literal["create"] = "create"
    project_id: Id
    blueprint_id: Id


class BeginInventionWork(InventionCommand):
    kind: Literal["begin"] = "begin"
    project_id: Id
    phase: InventionPhase
    units: int = Field(default=1, ge=1, le=10000)


class SettleInventionWork(InventionCommand):
    kind: Literal["settle"] = "settle"
    project_id: Id
    work_id: Id


class AbandonInvention(InventionCommand):
    kind: Literal["abandon"] = "abandon"
    project_id: Id


class PauseInventionWork(InventionCommand):
    kind: Literal["pause"] = "pause"
    project_id: Id
    work_id: Id


class ResumeInventionWork(InventionCommand):
    kind: Literal["resume"] = "resume"
    project_id: Id
    work_id: Id


TypedInventionCommand = Annotated[
    CreateInvention
    | BeginInventionWork
    | SettleInventionWork
    | AbandonInvention
    | PauseInventionWork
    | ResumeInventionWork,
    Field(discriminator="kind"),
]
COMMAND_ADAPTER: TypeAdapter[TypedInventionCommand] = TypeAdapter(TypedInventionCommand)


class InventionOutcome(Record):
    command_id: Id
    project_id: Id
    phase: InventionPhase
    status: str
    check: CheckTrace | None = None
    money_spent: int = Field(default=0, ge=0)
    materials_spent: tuple[MaterialRequirement, ...] = ()
    behavior_available: bool = False
    due: int | None = Field(default=None, ge=1)
    schedule_dice: tuple[int, ...] = ()
    schedule_kind: ScheduleKind | None = None
    defects: tuple[GadgetDefect, ...] = ()


_COMPLEXITY_MODIFIER = {"simple": -6, "average": -10, "complex": -14, "amazing": -22}
_GADGET_COMPLEXITY_MODIFIER = {"simple": 0, "average": -2, "complex": -4, "amazing": -8}
_GADGET_FACILITY_COST = {
    "simple": (50_000, 100_000),
    "average": (100_000, 250_000),
    "complex": (250_000, 500_000),
    "amazing": (500_000, 1_000_000),
}


def _digest(command: InventionCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(resources: ResourceState, command: InventionCommand) -> InventionOutcome | None:
    receipt = next((item for item in resources.receipts if item.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Invention command ID reused with a different payload")
    event = next(item for item in resources.events if item.id == "invention:" + command.id)
    return InventionOutcome.model_validate_json(event.kind)


def _blueprint(rules: InventionRules, identifier: str) -> InventionBlueprint:
    result = next((item for item in rules.blueprints if item.id == identifier), None)
    if result is None:
        raise ValidationError("Unknown authored invention blueprint")
    if result.method not in rules.permitted_methods:
        raise ValidationError("Invention method is not permitted by this campaign")
    return result


def _project(resources: ResourceState, identifier: str, actor_id: str) -> InventionProject:
    result = next((item for item in resources.inventions if item.id == identifier), None)
    if result is None or result.owner_id != actor_id:
        raise ValidationError("Invention project is not owned by this actor")
    return result


def _replace(resources: ResourceState, project: InventionProject) -> ResourceState:
    return resources.model_copy(
        update={
            "inventions": tuple(item for item in resources.inventions if item.id != project.id)
            + (project,)
        }
    )


def _record(
    state: PlayState,
    command: InventionCommand,
    project: InventionProject,
    outcome: InventionOutcome,
) -> PlayState:
    resources = _replace(state.resources, project)
    revision = state.revision + 1
    resources = resources.model_copy(
        update={
            "revision": revision,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": resources.events
            + (
                ResourceEvent(
                    id="invention:" + command.id,
                    at=resources.game_time,
                    target_id=project.id,
                    kind=outcome.model_dump_json(),
                ),
            ),
        }
    )
    return state.model_copy(update={"revision": revision, "resources": resources})


def _compiled_build(runtime: RulesContext, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next(item for item in state.actors if item.actor_id == actor_id)
    compiled, _ = runtime.reviewer.activate(
        actor.proposal,
        actor.approval,
        campaign_id=state.campaign_id,
        actor_id=actor_id,
    )
    return compiled


def _require_method_capability(
    runtime: RulesContext, state: PlayState, actor_id: str, blueprint: InventionBlueprint
) -> None:
    compiled = _compiled_build(runtime, state, actor_id)
    if not permits_gadgeteering(compiled, blueprint.method):
        raise ValidationError("Approved character lacks the required Gadgeteer capability")


def gadget_operation_allowed(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    blueprint: InventionBlueprint,
    operation: GadgetOperation,
) -> bool:
    """Decide device access without ever converting access into invention capability."""
    compiled = _compiled_build(runtime, state, actor_id)
    if gadgeteering_capability(compiled) is not None:
        return True
    if operation == "use":
        return blueprint.non_gadgeteer_access.use
    if operation == "repair":
        return blueprint.non_gadgeteer_access.repair
    if operation == "reproduce":
        return blueprint.non_gadgeteer_access.reproduce
    return False


def _skill_target(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    blueprint: InventionBlueprint,
    phase: InventionPhase,
) -> int:
    compiled = _compiled_build(runtime, state, actor_id)
    skill_ids = (
        (blueprint.operation_skill_id,)
        if phase == "testing"
        else (blueprint.invention_skill_id, *blueprint.related_skill_ids)
    )
    return min(int(level(compiled, skill_id).value) for skill_id in skill_ids)


def _modifiers(blueprint: InventionBlueprint, phase: InventionPhase) -> tuple[Modifier, ...]:
    if phase == "testing":
        return (
            Modifier(
                -3,
                "prototype operation test",
                "campaigns:b474:testing",
                "gurps-basic-set-4e-2004",
                ModifierKind.SITUATIONAL,
            ),
        )
    gadget = blueprint.method != "ordinary"
    values = [
        Modifier(
            (_GADGET_COMPLEXITY_MODIFIER if gadget else _COMPLEXITY_MODIFIER)[blueprint.complexity],
            "gadget complexity" if gadget else "invention complexity",
            "campaigns:b475:concept" if gadget else "campaigns:b473:complexity",
            "gurps-basic-set-4e-2004",
            ModifierKind.SITUATIONAL,
        )
    ]
    if blueprint.novelty_modifier and not (gadget and blueprint.novelty == "new-technology"):
        values.append(
            Modifier(
                blueprint.novelty_modifier,
                f"authored novelty: {blueprint.novelty}",
                "campaigns:b473:concept",
                "gurps-basic-set-4e-2004",
                ModifierKind.SITUATIONAL,
            )
        )
    if blueprint.description_modifier:
        values.append(
            Modifier(
                blueprint.description_modifier,
                "clear authored concept",
                "campaigns:b473:new-inventions",
                "gurps-basic-set-4e-2004",
                ModifierKind.SITUATIONAL,
            )
        )
    tl_gap = blueprint.native_tl - blueprint.inventor_tl
    if tl_gap > 0:
        values.append(
            Modifier(
                -5 * tl_gap,
                "higher-TL gadget" if gadget else "one TL beyond inventor",
                "campaigns:b475:concept" if gadget else "campaigns:b473:concept",
                "gurps-basic-set-4e-2004",
                ModifierKind.SITUATIONAL,
            )
        )
    return tuple(values)


def _stage(blueprint: InventionBlueprint, phase: InventionPhase) -> StageRequirement:
    if phase == "prototype":
        return blueprint.prototype
    if phase == "testing":
        return blueprint.testing
    if phase == "production":
        return blueprint.production
    return StageRequirement(work_seconds=blueprint.concept_work_seconds)


def _adjusted_retail_price(blueprint: InventionBlueprint) -> int:
    assert blueprint.retail_price is not None and blueprint.campaign_tl is not None
    gap = max(0, blueprint.native_tl - blueprint.campaign_tl)
    return int(blueprint.retail_price * (2 ** (gap + 1) - 1))


def _facility_due(blueprint: InventionBlueprint) -> int:
    if blueprint.method == "ordinary":
        return blueprint.facility_cost
    assert blueprint.campaign_tl is not None
    base, increment = _GADGET_FACILITY_COST[blueprint.complexity]
    total = base + increment * max(0, blueprint.native_tl - blueprint.campaign_tl)
    if blueprint.similar_facility_available:
        total //= 10
    if blueprint.method == "quick-gadgeteer":
        total //= 100
    return total


def _gadget_stage(blueprint: InventionBlueprint, phase: InventionPhase) -> StageRequirement:
    requirement = _stage(blueprint, phase)
    if phase == "prototype" and blueprint.method != "ordinary":
        money = _adjusted_retail_price(blueprint)
        if blueprint.method == "quick-gadgeteer":
            money //= 100
        return requirement.model_copy(update={"money": money})
    if phase == "production" and blueprint.method != "ordinary":
        return requirement.model_copy(update={"money": _adjusted_retail_price(blueprint)})
    return requirement


def _work_duration(
    runtime: RulesContext, blueprint: InventionBlueprint, phase: InventionPhase
) -> tuple[int, tuple[int, ...]]:
    if blueprint.activity == "analysis":
        dice = draw_dice(runtime.rng, 1)
        multiplier = 60 if blueprint.method == "quick-gadgeteer" else 600
        return dice[0] * multiplier, dice
    if blueprint.activity == "modification":
        dice = draw_dice(runtime.rng, 1)
        multiplier = 600 if blueprint.method == "quick-gadgeteer" else 3600
        return dice[0] * multiplier, dice
    if blueprint.method != "quick-gadgeteer" or phase not in ("concept-design", "prototype"):
        return _stage(blueprint, phase).work_seconds, ()
    if phase == "concept-design":
        dice = draw_dice(runtime.rng, 1)
        return dice[0] * 60, dice
    dice_count = {"simple": 2, "average": 1, "complex": 1, "amazing": 4}[blueprint.complexity]
    dice = draw_dice(runtime.rng, dice_count)
    total = sum(dice)
    if blueprint.complexity == "simple":
        seconds = total * 60
    elif blueprint.complexity == "average":
        seconds = max(1800, (total - 2) * 3600)
    elif blueprint.complexity == "complex":
        seconds = total * 3600
    else:
        seconds = total * 3600
    return seconds, dice


def _available(state: PlayState, actor_id: str, project_id: str) -> None:
    resources = state.resources
    if actor_id in busy_actor_ids(resources.inventions):
        raise ConflictError("Inventor already has active full-time project work")
    if any(
        task.status == "pending" and task.actor_id == actor_id for task in repair_tasks(resources)
    ):
        raise ConflictError("Inventor is committed to repair work")
    if any(
        task.status == "pending" and task.actor_id == actor_id for task in resources.recovery_tasks
    ):
        raise ConflictError("Inventor is committed to recovery work")
    if any(item.actor_id == actor_id for item in state.party.queue):
        raise ConflictError("Inventor already has queued subgroup activity")
    if any(
        project.id != project_id
        and project.owner_id == actor_id
        and project.active_work is not None
        for project in resources.inventions
    ):
        raise ConflictError("Inventor cannot perform incompatible full-time work")


def _facility(resources: ResourceState, actor_id: str, definition_id: str) -> None:
    if not any(
        item.owner_id == actor_id
        and item.definition_id == definition_id
        and item.ground is None
        and (item.condition is None or not item.condition.disabled)
        for item in resources.items
    ):
        raise ValidationError("Required invention facilities are unavailable")


def _spend(
    resources: ResourceState,
    blueprint: InventionBlueprint,
    requirement: StageRequirement,
    actor_id: str,
    *,
    units: int,
    facility_cost: int,
) -> tuple[ResourceState, int, tuple[MaterialRequirement, ...]]:
    money = requirement.money * units + facility_cost
    pool = next((item for item in resources.pools if item.id == blueprint.funding_pool_id), None)
    if pool is None or pool.injury is not None or pool.fatigue is not None:
        raise ValidationError("Invention funding pool is unavailable")
    materials = tuple(
        item.model_copy(update={"quantity": item.quantity * units})
        for item in requirement.materials
    )
    available: dict[str, int] = {}
    for item in resources.items:
        if item.owner_id == actor_id and item.ground is None and not item.equipped:
            available[item.definition_id] = available.get(item.definition_id, 0) + item.quantity
    if pool.current < money:
        raise ValidationError("Invention funding shortfall")
    if any(available.get(item.definition_id, 0) < item.quantity for item in materials):
        raise ValidationError("Invention material shortfall")
    # All shortfalls were checked before either ledger changes.
    remaining = {item.definition_id: item.quantity for item in materials}
    kept: list[Item] = []
    for item in resources.items:
        wanted = remaining.get(item.definition_id, 0)
        if wanted and item.owner_id == actor_id and item.ground is None and not item.equipped:
            used = min(wanted, item.quantity)
            remaining[item.definition_id] -= used
            if used < item.quantity:
                kept.append(item.model_copy(update={"quantity": item.quantity - used}))
        else:
            kept.append(item)
    pools = tuple(
        item.model_copy(update={"current": item.current - money}) if item.id == pool.id else item
        for item in resources.pools
    )
    return resources.model_copy(update={"items": tuple(kept), "pools": pools}), money, materials


def _concept_result(project: InventionProject, check: CheckTrace) -> tuple[dict[str, object], str]:
    updates: dict[str, object] = {"active_work": None}
    status = check.outcome.value
    if check.outcome.succeeded or check.outcome is Outcome.CRITICAL_FAILURE:
        updates["phase"] = "prototype"
        updates["flawed_theory"] = check.outcome is Outcome.CRITICAL_FAILURE
        status = "testable-theory" if check.outcome.succeeded else "flawed-theory"
    return updates, status


def _gadget_defect(
    runtime: RulesContext, blueprint: InventionBlueprint, project: InventionProject, index: int
) -> GadgetDefect:
    context = blueprint.gadget_context
    assert context is not None
    dice = draw_dice(runtime.rng, 3)
    total = sum(dice)
    kind: GadgetDefectKind
    magnitude: int | None = None
    if total == 3:
        kind = "unwanted-attention"
    elif total == 4:
        kind = "oversized"
    elif total in (5, 12):
        kind, magnitude = "resource-drain", 250 if total == 5 else 25
    elif total in (6, 8):
        kind = "side-effects"
        side_effect_die = draw_dice(runtime.rng, 1)[0]
        magnitude = side_effect_die + 1 if total == 6 else max(1, side_effect_die - 2)
    elif total == 7:
        kind, magnitude = "awkward", 2
    elif total == 9:
        kind = (
            "power-hungry"
            if context.powered
            else ("weapon-underperformance" if context.weapon else "oversized")
        )
    elif total == 10:
        kind = "weapon-underperformance" if context.weapon else "oversized"
    elif total == 11:
        kind, magnitude = "overheating", 600
    elif total in (13, 16):
        kind, magnitude = "unreliable", 14 if total == 13 else 10
    elif total == 14:
        kind = "repair-after-use"
    elif total == 15:
        kind = "recoil"
    elif total == 17:
        kind, magnitude = "preparation-required", 5 if context.weapon else 7200
    else:
        kind = "critical-self-destruction"
    return GadgetDefect(
        id=f"{project.id}:defect:{len(project.gadget_defects) + index + 1}",
        kind=kind,
        table_dice=dice,
        table_total=total,
        magnitude=magnitude,
    )


def _prototype_result(
    runtime: RulesContext,
    blueprint: InventionBlueprint,
    project: InventionProject,
    check: CheckTrace,
) -> tuple[dict[str, object], str]:
    updates: dict[str, object] = {"active_work": None}
    if project.flawed_theory:
        if check.outcome is Outcome.CRITICAL_SUCCESS:
            updates["status"] = "failed"
            return updates, "flawed-theory-discovered"
        return updates, "prototype-impossible"
    if check.outcome is Outcome.CRITICAL_FAILURE:
        if blueprint.method == "quick-gadgeteer":
            return updates, "prototype-parts-ruined"
        updates.update({"facility_paid": False, "facility_destroyed": True})
        return updates, "prototype-accident"
    if not check.outcome.succeeded:
        return updates, check.outcome.value
    updates["phase"] = "testing"
    if blueprint.method != "ordinary":
        bug_count = 0 if check.margin >= 3 else draw_dice(runtime.rng, 1)[0] // 2
        defects = tuple(
            _gadget_defect(runtime, blueprint, project, index)
            if blueprint.native_tl > blueprint.inventor_tl
            else GadgetDefect(
                id=f"{project.id}:defect:{len(project.gadget_defects) + index + 1}",
                kind="ordinary-minor",
            )
            for index in range(bug_count)
        )
        updates.update(
            {
                "minor_bugs": len(defects),
                "major_bugs": 0,
                "gadget_defects": defects,
            }
        )
        return updates, "prototype-built"
    if check.outcome is Outcome.CRITICAL_SUCCESS:
        minor = major = 0
    elif check.margin >= 3:
        minor, major = draw_dice(runtime.rng, 1)[0] // 2, 0
    else:
        major = draw_dice(runtime.rng, 1)[0] // 2
        minor = draw_dice(runtime.rng, 1)[0]
    updates.update({"minor_bugs": minor, "major_bugs": major})
    return updates, "prototype-built"


def _testing_result(
    project: InventionProject, check: CheckTrace, at: int
) -> tuple[dict[str, object], str]:
    if project.method != "ordinary":
        defects = list(project.gadget_defects)
        if check.outcome is Outcome.CRITICAL_SUCCESS:
            defects.clear()
        elif check.outcome.succeeded:
            discovered = next((i for i, item in enumerate(defects) if item.discovered), None)
            if discovered is not None:
                defects.pop(discovered)
        elif check.outcome is Outcome.FAILURE:
            hidden = next((i for i, item in enumerate(defects) if not item.discovered), None)
            if hidden is not None:
                defects[hidden] = defects[hidden].model_copy(
                    update={"discovered": True, "discovered_at": at}
                )
        gadget_updates: dict[str, object] = {
            "active_work": None,
            "minor_bugs": len(defects),
            "major_bugs": 0,
            "gadget_defects": tuple(defects),
        }
        if not defects and check.outcome.succeeded:
            gadget_updates["phase"] = "production"
            return gadget_updates, "testing-complete"
        if check.outcome is Outcome.CRITICAL_FAILURE:
            return gadget_updates, "false-clearance"
        if check.outcome is Outcome.FAILURE and any(item.discovered_at == at for item in defects):
            return gadget_updates, "gadget-bug-discovered"
        return gadget_updates, check.outcome.value
    minor, major = project.minor_bugs, project.major_bugs
    if check.outcome is Outcome.CRITICAL_SUCCESS:
        minor = major = 0
    elif check.outcome.succeeded and major:
        major -= 1
    elif check.outcome.succeeded and minor:
        minor -= 1
    updates: dict[str, object] = {
        "active_work": None,
        "minor_bugs": minor,
        "major_bugs": major,
    }
    if minor == 0 and major == 0 and check.outcome.succeeded:
        updates["phase"] = "production"
        return updates, "testing-complete"
    if check.outcome is Outcome.CRITICAL_FAILURE:
        return updates, "false-clearance"
    if check.outcome is Outcome.FAILURE and major:
        return updates, "major-bug-triggered"
    return updates, check.outcome.value


def _settle_check(
    runtime: RulesContext,
    state: PlayState,
    project: InventionProject,
    blueprint: InventionBlueprint,
) -> tuple[InventionProject, CheckTrace | None, str, bool]:
    work = project.active_work
    assert work is not None
    phase = work.phase
    if phase == "production":
        total = project.copies + work.units
        lot = ProductionLot(
            command_id=work.id,
            quantity=work.units,
            catalog_definition_id=blueprint.catalog_definition_id,
            runtime_adapter_id=blueprint.runtime_adapter_id,
            behavior_available=bool(
                blueprint.catalog_definition_id and blueprint.runtime_adapter_id
            ),
            defects=project.gadget_defects,
            non_gadgeteer_access=blueprint.non_gadgeteer_access,
        )
        complete = total >= blueprint.target_copies
        return (
            project.model_copy(
                update={
                    "phase": "complete" if complete else "production",
                    "status": "completed" if complete else "active",
                    "copies": total,
                    "active_work": None,
                    "lots": project.lots + (lot,),
                }
            ),
            None,
            "production-complete" if complete else "copy-produced",
            lot.behavior_available,
        )
    target = _skill_target(runtime, state, project.owner_id, blueprint, phase)
    check = success_roll(
        "gurps-basic-set-4e-2004",
        target,
        _modifiers(blueprint, phase),
        rng=runtime.rng,
    )
    if phase == "concept-design":
        updates, status = _concept_result(project, check)
    elif phase == "prototype":
        updates, status = _prototype_result(runtime, blueprint, project, check)
    else:
        updates, status = _testing_result(project, check, state.resources.game_time)
    if blueprint.activity == "analysis" and updates.get("phase") == "prototype":
        updates.update({"phase": "complete", "status": "completed"})
        status = "analysis-flawed" if updates.get("flawed_theory") else "analysis-complete"
    elif blueprint.activity == "modification" and updates.get("phase") == "testing":
        updates.update({"phase": "complete", "status": "completed"})
        status = "modification-complete"
    attempt = InventionAttempt(
        command_id=work.id,
        phase=phase,
        check=check,
        status=status,
        at=state.resources.game_time,
    )
    updates["attempts"] = project.attempts + (attempt,)
    settled = project.model_copy(update=updates)
    if not phase_after(project.phase, settled.phase):
        raise ValidationError("Invention phase cannot move backwards")
    return settled, check, status, False


def _schedule_kind(blueprint: InventionBlueprint) -> ScheduleKind:
    if blueprint.method == "ordinary":
        return "ordinary-full-time"
    if blueprint.method == "gadgeteer":
        return "gadgeteer-interruptible"
    return "quick-random"


def _begin(
    runtime: RulesContext,
    state: PlayState,
    command: BeginInventionWork,
    project: InventionProject,
    blueprint: InventionBlueprint,
) -> tuple[PlayState, InventionProject, InventionOutcome]:
    if project.status != "active" or project.active_work is not None:
        raise ConflictError("Invention is not available for new work")
    if command.phase != project.phase or command.phase == "complete":
        raise ConflictError("Invention work must match the current phase")
    if command.phase != "production" and command.units != 1:
        raise ValidationError("Only production work accepts multiple units")
    if command.phase == "production" and project.copies + command.units > blueprint.target_copies:
        raise ValidationError("Production units exceed the authored target")
    _available(state, command.actor_id, project.id)
    _skill_target(runtime, state, command.actor_id, blueprint, command.phase)
    resources = state.resources
    requirement = _gadget_stage(blueprint, command.phase)
    facility_due = command.phase == "prototype" and not project.facility_paid
    if command.phase in ("prototype", "testing", "production"):
        _facility(resources, command.actor_id, blueprint.facility_definition_id)
    resources, money, materials = _spend(
        resources,
        blueprint,
        requirement,
        command.actor_id,
        units=command.units,
        facility_cost=_facility_due(blueprint) if facility_due else 0,
    )
    duration, schedule_dice = _work_duration(runtime, blueprint, command.phase)
    work = InventionWork(
        id=command.id,
        phase=command.phase,
        start=resources.game_time,
        due=resources.game_time + duration * command.units,
        money_spent=money,
        materials_spent=materials,
        units=command.units,
        schedule_dice=schedule_dice,
        schedule_kind=_schedule_kind(blueprint),
    )
    project = project.model_copy(
        update={
            "active_work": work,
            "facility_paid": project.facility_paid or facility_due,
            "facility_destroyed": False if facility_due else project.facility_destroyed,
        }
    )
    state = state.model_copy(update={"resources": resources})
    return (
        state,
        project,
        InventionOutcome(
            command_id=command.id,
            project_id=project.id,
            phase=project.phase,
            status="work-started",
            money_spent=money,
            materials_spent=materials,
            due=work.due,
            schedule_dice=schedule_dice,
            schedule_kind=work.schedule_kind,
        ),
    )


def _settle(
    runtime: RulesContext,
    state: PlayState,
    command: SettleInventionWork,
    project: InventionProject,
    blueprint: InventionBlueprint,
) -> tuple[InventionProject, InventionOutcome]:
    work = project.active_work
    if work is None or work.id != command.work_id:
        raise ConflictError("Invention work receipt is not active")
    if work.status != "running":
        raise ConflictError("Paused invention work must resume before settlement")
    if state.resources.game_time < work.due:
        raise ConflictError("Invention work has not reached its shared-clock deadline")
    project, check, status, behavior = _settle_check(runtime, state, project, blueprint)
    return project, InventionOutcome(
        command_id=command.id,
        project_id=project.id,
        phase=project.phase,
        status=status,
        check=check,
        behavior_available=behavior,
        defects=project.gadget_defects,
    )


def _pause(
    state: PlayState,
    command: PauseInventionWork,
    project: InventionProject,
    blueprint: InventionBlueprint,
) -> tuple[InventionProject, InventionOutcome]:
    if blueprint.method == "ordinary":
        raise ValidationError("Ordinary invention work is not an interruptible adventure activity")
    work = project.active_work
    if work is None or work.id != command.work_id or work.status != "running":
        raise ConflictError("Running gadget work receipt is not active")
    now = state.resources.game_time
    if now >= work.due:
        raise ConflictError("Completed gadget work must be settled instead of paused")
    paused = work.model_copy(update={"status": "paused", "remaining_seconds": work.due - now})
    project = project.model_copy(update={"active_work": paused})
    return project, InventionOutcome(
        command_id=command.id,
        project_id=project.id,
        phase=project.phase,
        status="work-paused",
    )


def _resume(
    state: PlayState,
    command: ResumeInventionWork,
    project: InventionProject,
    blueprint: InventionBlueprint,
) -> tuple[InventionProject, InventionOutcome]:
    if blueprint.method == "ordinary":
        raise ValidationError("Ordinary invention work is not an interruptible adventure activity")
    work = project.active_work
    if work is None or work.id != command.work_id or work.status != "paused":
        raise ConflictError("Paused gadget work receipt is not active")
    assert work.remaining_seconds is not None
    _available(state, command.actor_id, project.id)
    due = state.resources.game_time + work.remaining_seconds
    resumed = work.model_copy(
        update={
            "start": state.resources.game_time,
            "due": due,
            "status": "running",
            "remaining_seconds": None,
        }
    )
    project = project.model_copy(update={"active_work": resumed})
    return project, InventionOutcome(
        command_id=command.id,
        project_id=project.id,
        phase=project.phase,
        status="work-resumed",
        due=due,
        schedule_dice=resumed.schedule_dice,
        schedule_kind=resumed.schedule_kind,
    )


def apply_invention(
    runtime: RulesContext,
    state: PlayState,
    command: TypedInventionCommand,
    *,
    system: bool = False,
) -> tuple[PlayState, InventionOutcome]:
    """Apply one project command. Replayed receipts return without costs, time or dice."""
    if not system:
        raise ValidationError("Invention projects require engine authority")
    rules = runtime.rules.inventions
    if rules is None:
        raise ValidationError("Campaign has no authored invention rules")
    prior = _prior(state.resources, command)
    if prior is not None:
        return state, prior
    if command.expected_revision != state.revision or state.revision != state.resources.revision:
        raise ConflictError("Invention project revision changed")
    if command.actor_id not in {actor.actor_id for actor in state.actors}:
        raise ValidationError("Inventor is not an approved campaign actor")
    if isinstance(command, CreateInvention):
        if any(item.id == command.project_id for item in state.resources.inventions):
            raise ConflictError("Invention project ID already exists")
        blueprint = _blueprint(rules, command.blueprint_id)
        _require_method_capability(runtime, state, command.actor_id, blueprint)
        initial_phase: InventionPhase = (
            "prototype" if blueprint.activity == "modification" else "concept-design"
        )
        _skill_target(runtime, state, command.actor_id, blueprint, initial_phase)
        project = InventionProject(
            id=command.project_id,
            owner_id=command.actor_id,
            blueprint_id=blueprint.id,
            method=blueprint.method,
            phase=initial_phase,
        )
        outcome = InventionOutcome(
            command_id=command.id,
            project_id=project.id,
            phase=project.phase,
            status="created",
        )
    else:
        project = _project(state.resources, command.project_id, command.actor_id)
        blueprint = _blueprint(rules, project.blueprint_id)
        _require_method_capability(runtime, state, command.actor_id, blueprint)
        if isinstance(command, AbandonInvention):
            if project.status != "active":
                raise ConflictError("Only an active invention can be abandoned")
            project = project.model_copy(update={"status": "abandoned", "active_work": None})
            outcome = InventionOutcome(
                command_id=command.id,
                project_id=project.id,
                phase=project.phase,
                status="abandoned",
            )
        elif isinstance(command, BeginInventionWork):
            state, project, outcome = _begin(runtime, state, command, project, blueprint)
        elif isinstance(command, PauseInventionWork):
            project, outcome = _pause(state, command, project, blueprint)
        elif isinstance(command, ResumeInventionWork):
            project, outcome = _resume(state, command, project, blueprint)
        else:
            assert isinstance(command, SettleInventionWork)
            project, outcome = _settle(runtime, state, command, project, blueprint)
    state = _record(state, command, project, outcome)
    return state, outcome
