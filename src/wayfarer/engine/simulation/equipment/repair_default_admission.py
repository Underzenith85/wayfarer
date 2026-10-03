"""Authenticated training observations and actor-selected default commitments."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.equipment.repair_default_resolver import derive, source_skill
from wayfarer.engine.simulation.equipment.repair_defaults import (
    TARGETS,
    ArmouryTraining,
    DeclareArmouryTraining,
    RepairDefaultSelection,
    SelectRepairDefault,
    record,
    selected,
)
from wayfarer.engine.simulation.equipment.repair_time import METHODS, SelectRepairTime
from wayfarer.engine.simulation.equipment.repair_time_selection import select as select_time
from wayfarer.engine.simulation.equipment.repair_transitions import repair
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def _save(state: PlayState, value: ArmouryTraining | RepairDefaultSelection) -> PlayState:
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "resources": record(state.resources, value).model_copy(
                update={"revision": state.revision + 1}
            ),
        }
    )


def declare_training(
    runtime: RulesContext, state: PlayState, command: DeclareArmouryTraining
) -> tuple[PlayState, ArmouryTraining]:
    compiled = build(runtime, state, command.performer_id)
    if compiled.revision != command.build_revision:
        raise ConflictError("Training facts require the current approved build revision")
    if (
        len(set(command.society_known_skills)) != len(command.society_known_skills)
        or not set(command.society_known_skills) <= TARGETS
    ):
        raise ValidationError("Training observations require concrete supported repair specialties")
    for identifier in command.society_known_skills:
        source_skill(runtime, identifier)
    facts = ArmouryTraining(
        command_id=command.id,
        performer_id=command.performer_id,
        build_revision=compiled.revision,
        personal_technology_level=command.personal_technology_level,
        society_known_skills=command.society_known_skills,
    )
    return _save(state, facts), facts


def select_default(
    runtime: RulesContext, state: PlayState, command: SelectRepairDefault
) -> tuple[PlayState, RepairDefaultSelection]:
    if (
        command.start_command_id == command.id
        or selected(state.resources, command.start_command_id) is not None
        or any(t.id == command.start_command_id for t in tasks(state.resources))
    ):
        raise ConflictError("Repair default selection is immutable for its start command")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None:
        raise ValidationError("Repair default requires a current item")
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    plan = derive(
        runtime,
        state,
        actor_id=command.actor_id,
        item=item,
        entry=entry,
        source_id=command.source_id,
        command_id=command.id,
        start_command_id=command.start_command_id,
        repair_time_method=command.repair_time_method,
    )
    repair(
        runtime,
        state,
        actor_id=command.actor_id,
        item_id=item.id,
        command_id=command.start_command_id,
        stage="start",
        task_id=None,
        preview=True,
        assessment_only=True,
        default_override=plan,
        selection_modifier=METHODS[command.repair_time_method][1]
        if command.repair_time_method is not None
        else 0,
    )
    if command.repair_time_method is None:
        return _save(state, plan), plan
    # Both declarations commit together; this temporary ledger is never externally visible.
    temporary = state.model_copy(update={"resources": record(state.resources, plan)})
    # The child preview resolves the declared proof but the matching time ledger is not
    # committed yet; pass the typed proof directly instead of relaxing current() checks.
    updated, _ = select_time(
        runtime,
        temporary,
        SelectRepairTime(
            id=command.id,
            actor_id=command.actor_id,
            item_id=command.item_id,
            start_command_id=command.start_command_id,
            method=command.repair_time_method,
            expected_revision=command.expected_revision,
        ),
        default_override=plan,
    )
    return updated, plan
