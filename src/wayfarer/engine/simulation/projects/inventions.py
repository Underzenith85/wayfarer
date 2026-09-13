"""Authored invention and gadgeteering project records (B473-477)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

InventionPhase = Literal["concept-design", "prototype", "testing", "production", "complete"]
Complexity = Literal["simple", "average", "complex", "amazing"]
InventionMethod = Literal["ordinary", "gadgeteer", "quick-gadgeteer"]
InventionActivity = Literal["invention", "analysis", "modification"]
GadgetOperation = Literal["use", "repair", "reproduce", "analyze", "modify", "invent"]
WorkStatus = Literal["running", "paused"]
ScheduleKind = Literal["ordinary-full-time", "gadgeteer-interruptible", "quick-random"]
Novelty = Literal[
    "original",
    "working-model",
    "known-device",
    "variant",
    "new-technology",
    "reinvention",
]


class GadgetContext(Record):
    """Authored physical context needed to interpret the B476 bug table."""

    powered: bool = False
    weapon: bool = False


class NonGadgeteerAccess(Record):
    """The campaign's explicit access decision for completed gadgets."""

    use: bool = True
    repair: bool = True
    reproduce: bool = False


GadgetDefectKind = Literal[
    "ordinary-minor",
    "unwanted-attention",
    "oversized",
    "resource-drain",
    "side-effects",
    "awkward",
    "power-hungry",
    "weapon-underperformance",
    "overheating",
    "unreliable",
    "repair-after-use",
    "recoil",
    "preparation-required",
    "critical-self-destruction",
]


class GadgetDefect(Record):
    """A hidden, replayable device defect selected only from recorded entropy."""

    id: Id
    kind: GadgetDefectKind
    table_dice: tuple[int, ...] = Field(default=(), min_length=0, max_length=3)
    table_total: int | None = Field(default=None, ge=3, le=18)
    magnitude: int | None = Field(default=None, ge=1)
    discovered: bool = False
    discovered_at: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def recorded_selection(self) -> GadgetDefect:
        if self.kind == "ordinary-minor":
            if self.table_dice or self.table_total is not None:
                raise ValueError("Ordinary minor bugs do not use the gadget bug table")
        elif len(self.table_dice) != 3 or sum(self.table_dice) != self.table_total:
            raise ValueError("Gadget defect must retain its selecting 3d roll")
        if self.discovered != (self.discovered_at is not None):
            raise ValueError("Gadget defect discovery needs an exact shared-clock time")
        return self


class MaterialRequirement(Record):
    definition_id: Id
    quantity: int = Field(ge=1)


class StageRequirement(Record):
    work_seconds: int = Field(ge=1)
    money: int = Field(default=0, ge=0)
    materials: tuple[MaterialRequirement, ...] = ()


class InventionBlueprint(Record):
    """Trusted concept, prerequisites, resources and method-specific context."""

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
    method: InventionMethod = "ordinary"
    activity: InventionActivity = "invention"
    subject_definition_id: Id | None = None
    campaign_tl: int | None = Field(default=None, ge=0, le=12)
    retail_price: int | None = Field(default=None, ge=0)
    similar_facility_available: bool = False
    gadget_context: GadgetContext | None = None
    non_gadgeteer_access: NonGadgeteerAccess = NonGadgeteerAccess()

    @model_validator(mode="after")
    def method_limits(self) -> InventionBlueprint:
        if self.method == "ordinary" and self.native_tl > self.inventor_tl + 1:
            raise ValueError("Ordinary invention cannot exceed inventor TL by more than one")
        if self.method != "ordinary" and (
            self.campaign_tl is None or self.retail_price is None or self.gadget_context is None
        ):
            raise ValueError("Gadget projects require campaign TL, retail price and device context")
        if self.activity != "invention" and self.method == "ordinary":
            raise ValueError("Analysis and modification are gadgeteering activities")
        if (self.activity == "invention") != (self.subject_definition_id is None):
            raise ValueError("Encountered-gadget activities require one authored subject")
        if self.method == "ordinary" and (
            self.similar_facility_available or self.gadget_context is not None
        ):
            raise ValueError("Ordinary inventions cannot claim gadgeteering context")
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
    permitted_methods: tuple[InventionMethod, ...] = ("ordinary",)

    @model_validator(mode="after")
    def unique_blueprints(self) -> InventionRules:
        if len({item.id for item in self.blueprints}) != len(self.blueprints):
            raise ValueError("Duplicate invention blueprint")
        if len(set(self.permitted_methods)) != len(self.permitted_methods):
            raise ValueError("Duplicate permitted invention method")
        if (
            "quick-gadgeteer" in self.permitted_methods
            and "gadgeteer" not in self.permitted_methods
        ):
            raise ValueError("Quick gadgeteering permission requires gadgeteering permission")
        if any(item.method not in self.permitted_methods for item in self.blueprints):
            raise ValueError("Invention blueprint method lacks explicit campaign permission")
        return self


class InventionWork(Record):
    id: Id
    phase: InventionPhase
    start: int = Field(ge=0)
    due: int = Field(gt=0)
    money_spent: int = Field(default=0, ge=0)
    materials_spent: tuple[MaterialRequirement, ...] = ()
    units: int = Field(default=1, ge=1)
    status: WorkStatus = "running"
    remaining_seconds: int | None = Field(default=None, ge=1)
    schedule_dice: tuple[int, ...] = ()
    schedule_kind: ScheduleKind = "ordinary-full-time"

    @model_validator(mode="after")
    def ordered(self) -> InventionWork:
        if self.due <= self.start:
            raise ValueError("Invention work requires a future deadline")
        if self.status == "paused" and self.remaining_seconds is None:
            raise ValueError("Paused invention work requires remaining work time")
        if self.status == "running" and self.remaining_seconds is not None:
            raise ValueError("Running invention work cannot retain paused time")
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
    defects: tuple[GadgetDefect, ...] = ()
    non_gadgeteer_access: NonGadgeteerAccess = NonGadgeteerAccess()

    @model_validator(mode="after")
    def binding_is_explicit(self) -> ProductionLot:
        if self.behavior_available != bool(self.catalog_definition_id and self.runtime_adapter_id):
            raise ValueError("Produced behavior disagrees with runtime bindings")
        return self


class InventionProject(Record):
    id: Id
    owner_id: Id
    blueprint_id: Id
    method: InventionMethod = "ordinary"
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
    gadget_defects: tuple[GadgetDefect, ...] = ()


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
        if project.method != blueprint.method:
            raise ValidationError("Invention project method differs from its authored blueprint")
        if project.method == "ordinary" and project.gadget_defects:
            raise ValidationError("Ordinary inventions cannot carry gadget-table defects")
        if project.minor_bugs != len(project.gadget_defects) and project.method != "ordinary":
            raise ValidationError("Gadget defect count disagrees with project state")
        if project.status == "completed" and project.phase != "complete":
            raise ValidationError("Completed invention must be in the complete phase")


def busy_actor_ids(projects: tuple[InventionProject, ...]) -> frozenset[str]:
    return frozenset(
        project.owner_id
        for project in projects
        if project.status == "active"
        and project.active_work is not None
        and project.active_work.status == "running"
    )


def phase_after(before: InventionPhase, after: InventionPhase) -> bool:
    return _PHASE_ORDER[after] >= _PHASE_ORDER[before]
