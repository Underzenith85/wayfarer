"""Admit an actor's finite Armoury work choice before parts RNG or consumption."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.equipment.repair_time import (
    METHODS,
    RepairTimeSelection,
    SelectRepairTime,
    record,
    selected,
)
from wayfarer.engine.simulation.equipment.repair_transitions import repair
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def select(
    runtime: RulesContext, state: PlayState, command: SelectRepairTime
) -> tuple[PlayState, RepairTimeSelection]:
    seconds, adjustment = METHODS[command.method]
    _, eligibility = repair(
        runtime,
        state,
        actor_id=command.actor_id,
        item_id=command.item_id,
        command_id=command.start_command_id,
        stage="start",
        task_id=None,
        preview=True,
        assessment_only=True,
        selection_modifier=adjustment,
    )
    if eligibility.procedure_id is None:
        raise ValidationError("Selected repair time requires a supported Armoury consumer")
    if (
        command.start_command_id == command.id
        or selected(state.resources, command.start_command_id) is not None
        or any(t.id == command.start_command_id for t in tasks(state.resources))
    ):
        raise ConflictError("Repair time selection is immutable for its start command")
    item = next(i for i in state.resources.items if i.id == command.item_id)
    assert item.condition is not None
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    plan = RepairTimeSelection(
        command_id=command.id,
        actor_id=command.actor_id,
        item_id=item.id,
        start_command_id=command.start_command_id,
        definition_id=item.definition_id,
        equipment_digest=digest(entry),
        condition=item.condition,
        method=command.method,
        duration_seconds=seconds,
        modifier=adjustment,
    )
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "resources": record(state.resources, plan).model_copy(
                update={"revision": state.revision + 1}
            ),
        }
    ), plan
