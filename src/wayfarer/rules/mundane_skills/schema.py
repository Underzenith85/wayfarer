"""Strict source-inventory records; audit-only flags are not runtime mechanics."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    specialty_required: bool = False
    tl_required: bool = False
    # A row may record no item-level blocker of its own once its procedure is
    # implemented; `inventory()` still carries the shared printing-delta audit,
    # so no row is ever unblocked here.
    blockers: tuple[Blocker, ...] = ()
    issues: Annotated[tuple[Annotated[int, Field(gt=0)], ...], Field(min_length=1)]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.attribute is None) != (self.difficulty is None):
            raise ValueError("Attribute and difficulty must be recorded together")
        if self.attribute is None and (
            self.attribute_defaults or self.skill_defaults or self.prerequisites or self.specialty
        ):
            raise ValueError("Structured mechanics require attribute and difficulty")
        if (
            self.specialty_required
            and not self.specialty
            and "specialty-expansion" not in self.blockers
        ):
            raise ValueError("Unexpanded required specialties need an explicit blocker")
        if self.tl_required and "technology-level-context" not in self.blockers:
            raise ValueError("Unimplemented TL context needs an explicit blocker")
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
