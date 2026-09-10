"""Strict source-inventory records; audit-only flags are not runtime mechanics.

A row may drop its ``runtime-procedure`` blocker only when an implemented
procedure covers it. :mod:`wayfarer.rules.mundane_skills.technology` is the single
authority for that, so a cleared blocker can never be asserted by the source
record alone. Contextual blockers stay owned by the source review either way.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wayfarer.rules.mundane_skills.technology import covers
from wayfarer.rules.skill_types import Difficulty

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


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AttributeDefault(Record):
    attribute: AttributeName
    modifier: int


class SkillDefaultRecord(Record):
    target: Identifier
    modifier: int


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
    alias_of: Identifier | None = None
    specialty_required: bool = False
    tl_required: bool = False
    blockers: tuple[Blocker, ...] = ()
    issues: Annotated[tuple[Annotated[int, Field(gt=0)], ...], Field(min_length=1)]
    procedure_owner: Annotated[int, Field(gt=0)]

    @property
    def covered(self) -> bool:
        """Whether an implemented procedure resolves this row, family or parent."""
        return covers(
            f"skill:{self.id}",
            self.specialty.family if self.specialty else None,
            f"skill:{self.technique.parent}" if self.technique else None,
        )

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
        if self.covered:
            if self.attribute is None:
                raise ValueError("A covered row must record its mechanics")
            if "runtime-procedure" in self.blockers:
                raise ValueError("A covered row cannot also block on its own runtime procedure")
        elif not self.blockers:
            raise ValueError("An uncovered row must record why it is unavailable")
        for values in (
            self.blockers,
            self.issues,
            self.prerequisites,
            tuple(d.attribute for d in self.attribute_defaults),
            tuple(d.target for d in self.skill_defaults),
        ):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate inventory metadata")
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
    baseline_reconciled: Literal[False]
    entries: tuple[SourceIndexEntry, ...]
