"""Authenticate a physical B345 kit choice for one future repair attempt."""

from wayfarer.engine.rules.skills.mundane.arts import OBJECT_REPAIR_SKILLS
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.firearm_repair_profile import require_profile
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.equipment.repair_transitions import _repair_binding
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.equipment.tool_context import (
    SELECTION_PREFIX,
    RepairToolSelection,
    SelectRepairTools,
    observation,
    record,
    require_observation,
    selected,
)
from wayfarer.engine.simulation.equipment.worksite import available_here
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def select(
    runtime: RulesContext, state: PlayState, command: SelectRepairTools
) -> tuple[PlayState, RepairToolSelection]:
    if (
        command.start_command_id == command.id
        or selected(state.resources, command.start_command_id) is not None
        or any(t.id == command.start_command_id for t in tasks(state.resources))
    ):
        raise ConflictError("Repair tool selection is immutable for its future start")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if (
        item is None
        or item.owner_id != command.actor_id
        or item.ground
        or item.equipped
        or not available_here(state, command.actor_id, item)
        or item.condition is None
        or item.condition.destroyed
    ):
        raise ValidationError("Repair tool selection needs current owned damaged equipment")
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    profile = entry.durability
    if (
        profile is None
        or not profile.repair_tools_definition
        or profile.repair_skill_id not in (*OBJECT_REPAIR_SKILLS, "skill:armoury-small-arms")
        or (item.condition.hp == profile.hp and not item.condition.disabled)
    ):
        raise ValidationError("Repair tool selection needs a supported Armoury consumer")
    _repair_binding(
        entry, profile.repair_skill_id, profile.repair_skill_id == "skill:armoury-small-arms"
    )
    if profile.repair_skill_id == "skill:armoury-small-arms":
        require_profile(entry)
    value = observation(state.resources, command.tool_id)
    if value is None:
        raise ValidationError(
            "Repair tool selection requires an authenticated physical observation"
        )
    tool = require_observation(runtime, state, command.actor_id, value, starting=True)
    if tool.definition_id != profile.repair_tools_definition:
        raise ValidationError("Selected repair kit does not match the pinned item binding")
    plan = RepairToolSelection(
        command_id=command.id,
        actor_id=command.actor_id,
        item_id=item.id,
        start_command_id=command.start_command_id,
        definition_id=item.definition_id,
        equipment_digest=digest(entry),
        condition=item.condition,
        observation=value,
    )
    resources = record(
        state.resources, SELECTION_PREFIX, plan, command.id, command.actor_id
    ).model_copy(update={"revision": state.revision + 1})
    return state.model_copy(update={"revision": state.revision + 1, "resources": resources}), plan
