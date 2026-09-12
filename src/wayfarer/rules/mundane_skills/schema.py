"""Strict source-inventory records; audit-only flags are not runtime mechanics."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Record
from wayfarer.rules.skill_types import DefaultConditionKind, Difficulty

AttributeName = Literal["IQ", "DX", "HT", "ST", "Will", "Per", "Perception"]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]
Blocker = Literal[
    "combat-procedure",
    "conditional-or-skill-defaults",
    "family-specialty-expansion",
    "prerequisite-procedure",
    "runtime-procedure",
    "specialty-expansion",
    "technology-level-context",
    "variable-family-metadata",
    "weapon-default-audit",
    "technique-expansion",
    "optional-rule-selection",
]


class DefaultConditionRecord(Record):
    kind: DefaultConditionKind
    value: Annotated[str, Field(pattern=r"^equipment:[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")] | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        needs_value = self.kind is DefaultConditionKind.REQUIRED_EQUIPMENT
        if needs_value != (self.value is not None):
            raise ValueError("Only a required-equipment condition names a value")
        return self


class AttributeDefault(Record):
    attribute: AttributeName
    modifier: int
    conditions: tuple[DefaultConditionRecord, ...] = ()

    @model_validator(mode="after")
    def distinct_conditions(self) -> Self:
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("Duplicate default condition")
        return self


class SkillDefaultRecord(Record):
    target: Identifier
    modifier: int
    conditions: tuple[DefaultConditionRecord, ...] = ()

    @model_validator(mode="after")
    def distinct_conditions(self) -> Self:
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("Duplicate default condition")
        return self


class SpecialtyRecord(Record):
    family: Identifier
    name: Identifier
    optional_parent: Identifier | None = None


class TechniqueRecord(Record):
    parent: Identifier
    default_modifier: int
    maximum_modifier: int = 0

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.maximum_modifier < self.default_modifier:
            raise ValueError("Technique maximum is below its default")
        return self


class PrerequisiteGroupRecord(Record):
    """An alternative set; satisfying any one member satisfies the requirement."""

    alternatives: Annotated[tuple[Identifier, ...], Field(min_length=2)]

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.alternatives)) != len(self.alternatives):
            raise ValueError("Duplicate prerequisite alternative")
        return self


class TechniqueTemplateRecord(Record):
    """A B230-233 technique listing before a concrete parent is chosen.

    A template is not a rollable skill, so it records no controlling attribute of
    its own unless the source overrides the parent's, as ST-based Neck Snap does.
    """

    difficulty: Difficulty
    default_modifier: int
    maximum_modifier: int = 0
    parents: tuple[Identifier, ...] = ()
    parent_family: Identifier | None = None
    attribute: AttributeName | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.maximum_modifier < self.default_modifier:
            raise ValueError("Technique maximum is below its default")
        if not self.parents and self.parent_family is None:
            raise ValueError("A technique template needs permitted parents")
        if len(set(self.parents)) != len(self.parents):
            raise ValueError("Duplicate permitted parent")
        return self


class VariableFamilyRecord(Record):
    """A family whose specialties the player defines; no list can enumerate them."""

    subject: Annotated[str, Field(min_length=1)]
    determination: Literal["mirrors-parent", "chosen-with-subject"]
    mirrors: Identifier | None = None
    attribute: AttributeName | None = None
    difficulty: Difficulty | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.determination == "mirrors-parent") != (self.mirrors is not None):
            raise ValueError("Only a mirroring family names the family it mirrors")
        # An open family may fix its difficulty while leaving the controlling
        # attribute to the subject the player names, but not the reverse.
        if self.attribute is not None and self.difficulty is None:
            raise ValueError("A recorded attribute needs its difficulty")
        return self


class InventoryRow(Record):
    id: Identifier
    name: Annotated[str, Field(min_length=1)]
    page: Annotated[int, Field(ge=168, le=233)]
    attribute: AttributeName | None = None
    difficulty: Difficulty | None = None
    attribute_defaults: tuple[AttributeDefault, ...] = ()
    skill_defaults: tuple[SkillDefaultRecord, ...] = ()
    prerequisites: tuple[Identifier, ...] = ()
    specialty: SpecialtyRecord | None = None
    technique: TechniqueRecord | None = None
    template: TechniqueTemplateRecord | None = None
    variable: VariableFamilyRecord | None = None
    prerequisite_groups: tuple[PrerequisiteGroupRecord, ...] = ()
    alias_of: Identifier | None = None
    specialty_required: bool = False
    tl_required: bool = False
    # A row whose contextual blockers are all resolved may have an empty list;
    # the selected source baseline is tracked separately from mechanics gaps.
    # a claim that the row is clean.
    blockers: tuple[Blocker, ...] = ()
    issues: Annotated[tuple[Annotated[int, Field(gt=0)], ...], Field(min_length=1)]
    procedure_owner: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.attribute is None) != (self.difficulty is None):
            raise ValueError("Attribute and difficulty must be recorded together")
        if self.attribute is None and (
            self.attribute_defaults
            or self.skill_defaults
            or self.prerequisites
            or self.specialty
            or self.technique
        ):
            raise ValueError("Structured mechanics require attribute and difficulty")
        if self.technique and (
            self.attribute_defaults
            or self.skill_defaults
            or self.specialty
            or self.difficulty not in (Difficulty.AVERAGE, Difficulty.HARD)
        ):
            raise ValueError("Technique mechanics must be parent-relative")
        if sum(x is not None for x in (self.technique, self.template, self.variable)) > 1:
            raise ValueError("A row is a technique, a template or a variable family")
        if self.template is not None and (self.attribute is not None or self.specialty is not None):
            raise ValueError("A technique template records no concrete mechanics")
        # An open family may still fix the numbers every subject rolls against
        # (B180 Biology is IQ/VH whichever planet type it covers), but it never
        # names one of them, and it records those numbers only once.
        if self.variable is not None:
            if self.specialty is not None:
                raise ValueError("An open family names no concrete specialty")
            if self.attribute is not None and (
                self.variable.attribute is not None or self.variable.difficulty is not None
            ):
                raise ValueError("An open family records its mechanics once")
        if self.prerequisite_groups and self.attribute is None:
            raise ValueError("Structured mechanics require attribute and difficulty")
        for group in self.prerequisite_groups:
            if self.id in group.alternatives or set(group.alternatives) & set(self.prerequisites):
                raise ValueError("An alternative cannot repeat the row or a firm prerequisite")
        if self.procedure_owner not in self.issues or self.procedure_owner in (112, 191, 336):
            raise ValueError("Every row needs a named procedure owner beyond the audit")
        if 336 not in self.issues:
            raise ValueError("Provisional metadata must retain its source/context owner")
        if (
            self.specialty_required
            and not self.specialty
            and "specialty-expansion" not in self.blockers
        ):
            raise ValueError("Unexpanded required specialties need an explicit blocker")
        if self.tl_required and "technology-level-context" not in self.blockers:
            raise ValueError("Unimplemented TL context needs an explicit blocker")
        for metadata in (
            self.blockers,
            self.issues,
            self.prerequisites,
        ):
            if len(set(metadata)) != len(metadata):
                raise ValueError("Duplicate inventory metadata")
        for defaults in (
            tuple((d.attribute, d.conditions) for d in self.attribute_defaults),
            tuple((d.target, d.conditions) for d in self.skill_defaults),
        ):
            if len(set(defaults)) != len(defaults):
                raise ValueError("Duplicate inventory default")
        return self


class Exclusion(Record):
    id: Identifier
    name: Annotated[str, Field(min_length=1)]
    page: Annotated[int, Field(ge=174, le=228)]
    reason: Annotated[str, Field(min_length=1)]
    # The owning follow-up issues that must resolve the transferred skill. An
    # exclusion without a named owner would silently drop it from the Basic Set.
    owners: Annotated[tuple[Annotated[int, Field(gt=0)], ...], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct_owners(self) -> Self:
        if len(set(self.owners)) != len(self.owners):
            raise ValueError("Duplicate exclusion owner")
        return self


class Exclusions(Record):
    scope: Annotated[str, Field(min_length=1)]
    excluded: tuple[Exclusion, ...]


class SourceIndexEntry(Record):
    id: Identifier
    name: Annotated[str, Field(min_length=1)]
    page: Annotated[int, Field(ge=168, le=233)]
    kind: Literal["skill", "technique", "expansion"]
    targets: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    parent: Identifier | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.kind == "expansion") != (self.parent is not None):
            raise ValueError("Only an expansion must identify its source parent")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("Duplicate source index targets")
        return self


class SourceIndex(Record):
    observed_source: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    index_reference: Annotated[str, Field(min_length=1)]
    baseline_reconciled: Literal[True]
    entries: tuple[SourceIndexEntry, ...]
