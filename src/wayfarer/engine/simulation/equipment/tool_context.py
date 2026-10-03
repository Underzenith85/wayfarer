"""Private B345 observations and immutable physical tool choices."""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.repair_defaults import training
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.equipment.worksite import available_here
from wayfarer.engine.simulation.resources import Command, Item, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

OBSERVATION_PREFIX = "armoury-tools:"
SELECTION_PREFIX = "armoury-tool-selection:"
Quality = Literal["basic", "good", "fine", "best"]
Damage = Literal["none", "minor", "moderate", "severe"]


class DeclareRepairTools(Command):
    kind: Literal["declare-repair-tools"] = "declare-repair-tools"
    performer_id: Id
    tool_id: Id
    quality: Quality
    missing_important_components: int = Field(default=0, ge=0)
    damage: Damage = "none"
    tool_technology_level: int | None = Field(default=None, ge=0, le=12)


class SelectRepairTools(Command):
    kind: Literal["select-repair-tools"] = "select-repair-tools"
    item_id: Id
    tool_id: Id
    start_command_id: Id


class RepairToolObservation(Record):
    command_id: Id
    performer_id: Id
    tool_id: Id
    definition_id: Id
    equipment_digest: str
    condition: ObjectCondition | None
    quality: Quality
    missing_important_components: int = Field(ge=0)
    damage: Damage
    tool_technology_level: int | None
    training_command_id: Id | None
    modifier: int

    @model_validator(mode="after")
    def canonical_modifier(self) -> RepairToolObservation:
        if self.quality == "best":
            if self.tool_technology_level is None or self.training_command_id is None:
                raise ValueError("Best tool observations require explicit TL provenance")
            quality = max(2, self.tool_technology_level // 2)
        else:
            if self.tool_technology_level is not None or self.training_command_id is not None:
                raise ValueError("Only best tool observations carry TL provenance")
            quality = {"basic": 0, "good": 1, "fine": 2}[self.quality]
        if (
            self.modifier
            != quality
            - self.missing_important_components
            - {"none": 0, "minor": 1, "moderate": 2, "severe": 3}[self.damage]
        ):
            raise ValueError("Tool modifier must equal its exact source-derived physical facts")
        return self


class RepairToolSelection(Record):
    command_id: Id
    actor_id: Id
    item_id: Id
    start_command_id: Id
    definition_id: Id
    equipment_digest: str
    condition: ObjectCondition
    observation: RepairToolObservation


def record(
    resources: ResourceState, prefix: str, value: Record, command_id: str, actor: str
) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=prefix + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=actor,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def observation(resources: ResourceState, tool_id: str) -> RepairToolObservation | None:
    return next(
        (
            p
            for e in reversed(resources.events)
            if e.id.startswith(OBSERVATION_PREFIX)
            and (p := RepairToolObservation.model_validate_json(e.kind)).tool_id == tool_id
        ),
        None,
    )


def selected(resources: ResourceState, start_id: str) -> RepairToolSelection | None:
    return next(
        (
            p
            for e in reversed(resources.events)
            if e.id.startswith(SELECTION_PREFIX)
            and (p := RepairToolSelection.model_validate_json(e.kind)).start_command_id == start_id
        ),
        None,
    )


def tool_here(
    runtime: RulesContext, state: PlayState, actor: str, tool_id: str
) -> tuple[Item, EquipmentProfile]:
    tool = next((i for i in state.resources.items if i.id == tool_id), None)
    if (
        tool is None
        or tool.owner_id != actor
        or tool.ground
        or not available_here(state, actor, tool)
        or (tool.condition is not None and (tool.condition.disabled or tool.condition.destroyed))
    ):
        raise ValidationError("Selected repair tool is unavailable")
    entry = next(e for e in catalog(runtime).entries if e.definition_id == tool.definition_id)
    features = tuple(f for f in entry.general if f.kind == "tool")
    if len({f.skill_id for f in features}) != len(features):
        raise ValidationError("Observed repair tools have ambiguous skill features")
    if any(
        f.modifier != 0 or f.consumable_definition_id is not None or f.duration_seconds is not None
        for f in features
    ):
        raise ValidationError(
            "Observed tools require a basic pinned profile without operating limits"
        )
    return tool, entry


def require_observation(
    runtime: RulesContext,
    state: PlayState,
    actor: str,
    value: RepairToolObservation,
    *,
    starting: bool,
) -> Item:
    tool, entry = tool_here(runtime, state, actor, value.tool_id)
    if (
        value.performer_id != actor
        or value.definition_id != tool.definition_id
        or value.equipment_digest != digest(entry)
        or value.condition != tool.condition
        or observation(state.resources, tool.id) != value
    ):
        raise ConflictError("Repair tool observation no longer matches current physical equipment")
    if value.quality == "best" and (
        not isinstance(entry.technology_level, int)
        or entry.technology_level != value.tool_technology_level
    ):
        raise ConflictError("Best equipment requires its exact pinned physical numeric TL")
    if starting and value.quality == "best":
        facts = training(state.resources, actor)
        compiled = runtime.approved_build(state, actor)
        if (
            facts is None
            or facts.command_id != value.training_command_id
            or facts.build_revision != compiled.revision
            or facts.personal_technology_level != value.tool_technology_level
        ):
            raise ConflictError(
                "Best repair equipment requires current matching verified personal TL"
            )
    return tool


def current(
    runtime: RulesContext,
    state: PlayState,
    actor: str,
    item: Item,
    entry: EquipmentProfile,
    start_id: str,
    *,
    starting: bool = True,
    repair_training_tl: int | None = None,
) -> RepairToolSelection | None:
    plan = selected(state.resources, start_id)
    if plan is None:
        return None
    if (
        plan.actor_id != actor
        or plan.item_id != item.id
        or plan.definition_id != item.definition_id
        or plan.equipment_digest != digest(entry)
        or plan.condition != item.condition
    ):
        raise ConflictError("Selected repair tools no longer match current repair equipment")
    tool = require_observation(runtime, state, actor, plan.observation, starting=starting)
    if entry.durability is None or tool.definition_id != entry.durability.repair_tools_definition:
        raise ConflictError("Selected tools do not match the pinned repair binding")
    if starting and plan.observation.quality == "best":
        if (
            entry.technology_level != plan.observation.tool_technology_level
            or repair_training_tl != plan.observation.tool_technology_level
        ):
            raise ValidationError(
                "Best tool quality requires matching physical, item, and repair training TL"
            )
    return plan


def declare(
    runtime: RulesContext, state: PlayState, command: DeclareRepairTools
) -> tuple[PlayState, RepairToolObservation]:
    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Tool observations require the Basic Set profile")
    tool, entry = tool_here(runtime, state, command.performer_id, command.tool_id)
    facts = None
    if command.quality == "best":
        facts = training(state.resources, command.performer_id)
        compiled = runtime.approved_build(state, command.performer_id)
        if (
            facts is None
            or facts.build_revision != compiled.revision
            or command.tool_technology_level != facts.personal_technology_level
        ):
            raise ValidationError(
                "Best equipment needs explicit physical TL matching verified personal TL"
            )
        if (
            not isinstance(entry.technology_level, int)
            or entry.technology_level != command.tool_technology_level
        ):
            raise ValidationError("Observed best equipment contradicts its pinned technological TL")
    elif command.tool_technology_level is not None:
        raise ValidationError("A tool TL observation is used only for best equipment")
    quality = (
        max(2, command.tool_technology_level // 2)
        if command.quality == "best" and command.tool_technology_level is not None
        else {"basic": 0, "good": 1, "fine": 2}[command.quality]
    )
    value = RepairToolObservation(
        command_id=command.id,
        performer_id=command.performer_id,
        tool_id=tool.id,
        definition_id=tool.definition_id,
        equipment_digest=digest(entry),
        condition=tool.condition,
        quality=command.quality,
        missing_important_components=command.missing_important_components,
        damage=command.damage,
        tool_technology_level=command.tool_technology_level,
        training_command_id=facts.command_id if facts else None,
        modifier=quality
        - command.missing_important_components
        - {"none": 0, "minor": 1, "moderate": 2, "severe": 3}[command.damage],
    )
    resources = record(
        state.resources, OBSERVATION_PREFIX, value, command.id, command.performer_id
    ).model_copy(update={"revision": state.revision + 1})
    return state.model_copy(update={"revision": state.revision + 1, "resources": resources}), value
