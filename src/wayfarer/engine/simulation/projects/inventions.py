"""Authored ordinary-invention rules and persisted project records (B473-475)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

InventionPhase = Literal["concept-design", "prototype", "testing", "production", "complete"]
Complexity = Literal["simple", "average", "complex", "amazing"]
Novelty = Literal[
    "original",
    "working-model",
    "known-device",
    "variant",
    "new-technology",
    "reinvention",
]


class MaterialRequirement(Record):
    definition_id: Id
    quantity: int = Field(ge=1)


class StageRequirement(Record):
    work_seconds: int = Field(ge=1)
    money: int = Field(default=0, ge=0)
    materials: tuple[MaterialRequirement, ...] = ()


class InventionBlueprint(Record):
    """Trusted GM-authored concept, prerequisites, costs and ordinary work schedule."""

    id: Id
    title: str = Field(min_length=1, max_length=200)
    concept: str = Field(min_length=1, max_length=2000)
    invention_skill_id: Id
    related_skill_ids: tuple[Id, ...] = ()
    operation_skill_id: Id
    complexity: Complexity
    novelty: Novelty = "original"
    novelty_modifier: int = Field(default=0, ge=-10, le=10)
    description_modifier: int = Field(default=0, ge=0, le=2)
    facility_definition_id: Id
    funding_pool_id: Id
    facility_cost: int = Field(ge=0)
    concept_work_seconds: int = Field(default=86400, ge=1)
    prototype: StageRequirement
    testing: StageRequirement = StageRequirement(work_seconds=604800)
    production: StageRequirement
    target_copies: int = Field(default=1, ge=1, le=10000)
    native_tl: int = Field(ge=0, le=12)
    inventor_tl: int = Field(ge=0, le=12)
    catalog_definition_id: str | None = None
    runtime_adapter_id: str | None = None
    method: Literal["ordinary"] = "ordinary"

    @model_validator(mode="after")
    def ordinary_limits(self) -> InventionBlueprint:
        if self.native_tl > self.inventor_tl + 1:
            raise ValueError("Ordinary invention cannot exceed inventor TL by more than one")
        if len(set(self.related_skill_ids)) != len(self.related_skill_ids):
            raise ValueError("Duplicate related invention skill")
        if (self.catalog_definition_id is None) != (self.runtime_adapter_id is None):
            raise ValueError("Device behavior requires both catalog and runtime bindings")
        return self


class InventionRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    blueprints: tuple[InventionBlueprint, ...] = ()

    @model_validator(mode="after")
    def unique_blueprints(self) -> InventionRules:
        if len({item.id for item in self.blueprints}) != len(self.blueprints):
            raise ValueError("Duplicate invention blueprint")
        return self


class InventionWork(Record):
    id: Id
    phase: InventionPhase
    start: int = Field(ge=0)
    due: int = Field(gt=0)
    money_spent: int = Field(default=0, ge=0)
    materials_spent: tuple[MaterialRequirement, ...] = ()
    units: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def ordered(self) -> InventionWork:
        if self.due <= self.start:
            raise ValueError("Invention work requires a future deadline")
        return self


class InventionAttempt(Record):
    command_id: Id
    phase: InventionPhase
    check: CheckTrace | None = None
    status: str
    at: int = Field(ge=0)


class ProductionLot(Record):
    command_id: Id
    quantity: int = Field(ge=1)
    catalog_definition_id: str | None = None
    runtime_adapter_id: str | None = None
    behavior_available: bool = False

    @model_validator(mode="after")
    def binding_is_explicit(self) -> ProductionLot:
        if self.behavior_available != bool(self.catalog_definition_id and self.runtime_adapter_id):
            raise ValueError("Produced behavior disagrees with runtime bindings")
        return self


class InventionProject(Record):
    id: Id
    owner_id: Id
    blueprint_id: Id
    phase: InventionPhase = "concept-design"
    status: Literal["active", "failed", "abandoned", "completed"] = "active"
    flawed_theory: bool = False
    facility_paid: bool = False
    facility_destroyed: bool = False
    minor_bugs: int = Field(default=0, ge=0)
    major_bugs: int = Field(default=0, ge=0)
    copies: int = Field(default=0, ge=0)
    active_work: InventionWork | None = None
    attempts: tuple[InventionAttempt, ...] = ()
    lots: tuple[ProductionLot, ...] = ()


_PHASE_ORDER = {
    name: index
    for index, name in enumerate(
        ("concept-design", "prototype", "testing", "production", "complete")
    )
}


def validate_projects(
    projects: tuple[InventionProject, ...], rules: InventionRules | None, game_time: int
) -> None:
    if len({project.id for project in projects}) != len(projects):
        raise ValidationError("Duplicate invention project")
    if projects and rules is None:
        raise ValidationError("Invention state requires authored invention rules")
    blueprints = {item.id: item for item in rules.blueprints} if rules else {}
    for project in projects:
        blueprint = blueprints.get(project.blueprint_id)
        if blueprint is None:
            raise ValidationError("Invention project has no authored blueprint")
        if project.copies > blueprint.target_copies:
            raise ValidationError("Invention production exceeds its authored target")
        if project.active_work is not None:
            if project.status != "active" or project.active_work.phase != project.phase:
                raise ValidationError("Invention work must match an active project phase")
            if project.active_work.start > game_time:
                raise ValidationError("Invention work cannot start in the future")
        if project.status == "completed" and project.phase != "complete":
            raise ValidationError("Completed invention must be in the complete phase")


def busy_actor_ids(projects: tuple[InventionProject, ...]) -> frozenset[str]:
    return frozenset(
        project.owner_id
        for project in projects
        if project.status == "active" and project.active_work is not None
    )


def phase_after(before: InventionPhase, after: InventionPhase) -> bool:
    return _PHASE_ORDER[after] >= _PHASE_ORDER[before]
