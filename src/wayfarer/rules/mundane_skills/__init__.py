"""Dedicated provisional Basic Set mundane skill inventory (#112).

Source-indexed metadata remains provisional for the frozen first-printing profile.
Printing deltas and item-level mechanics blockers are explicit audit data.
No existing package pin is changed and unimplemented runtime skills fail closed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path

from pydantic import TypeAdapter

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
)
from wayfarer.rules.gurps_characters import source
from wayfarer.rules.gurps_skills import definitions as representative_definitions
from wayfarer.rules.mundane_skills.ranged import OWNER as RANGED_OWNER
from wayfarer.rules.mundane_skills.ranged import PROCEDURES as RANGED_PROCEDURES
from wayfarer.rules.mundane_skills.schema import Exclusion, Exclusions, InventoryRow
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import SkillDefault, SkillPrerequisite, SkillSpec, Specialty

PROFILE = "gurps-basic-set-4e-2004"
SOURCE = source(PROFILE)
OWNER = 112


class StructuralClass(StrEnum):
    """Audit shapes an inventory row can take; every class must be sampled."""

    LISTING_ONLY = "listing-only"
    ATTRIBUTE_DEFAULT = "attribute-default"
    SKILL_DEFAULT = "skill-default"
    NO_DEFAULT = "no-default"
    PREREQUISITE = "prerequisite"
    OPTIONAL_SPECIALTY = "optional-specialty"
    REQUIRED_SPECIALTY = "required-specialty"
    UNEXPANDED_SPECIALTY = "unexpanded-specialty"
    TECHNIQUE = "technique"
    TECHNOLOGY_LEVEL = "technology-level"


@dataclass(frozen=True)
class SkillAudit:
    id: str
    name: str
    reference: str
    definition: RuleDefinition | None
    blockers: tuple[str, ...]
    followup_issues: tuple[int, ...]
    specialty_required: bool = False
    tl_required: bool = False
    # True only when a runtime module binds this row to a dispatch that already
    # resolves it. Recording mechanics never sets it. A family row is bound by
    # its concrete specialties and still has no dispatch of its own.
    bound: bool = False
    dispatch: str | None = None
    provenance: str = (
        "Characters Fourth Edition, third printing; first-printing delta audit pending"
    )

    @property
    def available(self) -> bool:
        return (
            not self.blockers
            and self.definition is not None
            and self.definition.status is ImplementationStatus.IMPLEMENTED
        )

    @property
    def implementation(self) -> str:
        """Certification state of this row, never a family-level claim."""
        if self.definition is None:
            return "listing-only"
        return "implemented" if self.bound else "unsupported"

    @property
    def owners(self) -> tuple[int, ...]:
        """Named mechanics issues other than this inventory's own accounting."""
        return tuple(issue for issue in self.followup_issues if issue != OWNER)

    @property
    def structural_classes(self) -> tuple[StructuralClass, ...]:
        """Classify recorded structure only; absent metadata is never inferred."""
        spec = self.definition.skill if self.definition else None
        found = set()
        if spec is None:
            found.add(StructuralClass.LISTING_ONLY)
        else:
            if not spec.defaults:
                found.add(StructuralClass.NO_DEFAULT)
            if any(default.target in A for default in spec.defaults):
                found.add(StructuralClass.ATTRIBUTE_DEFAULT)
            if any(default.target not in A for default in spec.defaults):
                found.add(StructuralClass.SKILL_DEFAULT)
            if spec.prerequisites:
                found.add(StructuralClass.PREREQUISITE)
            if spec.specialty is not None:
                found.add(
                    StructuralClass.OPTIONAL_SPECIALTY
                    if spec.specialty.optional_parent
                    else StructuralClass.REQUIRED_SPECIALTY
                )
            if spec.technique is not None:
                found.add(StructuralClass.TECHNIQUE)
        if self.specialty_required and (spec is None or spec.specialty is None):
            found.add(StructuralClass.UNEXPANDED_SPECIALTY)
        if self.tl_required:
            found.add(StructuralClass.TECHNOLOGY_LEVEL)
        return tuple(sorted(found))


def source_inventory() -> tuple[InventoryRow, ...]:
    return TypeAdapter(tuple[InventoryRow, ...]).validate_json(
        Path(__file__).with_name("inventory.json").read_text()
    )


def exclusions() -> tuple[Exclusion, ...]:
    rows = Exclusions.model_validate_json(Path(__file__).with_name("exclusions.json").read_text())
    names = [e.name.casefold() for e in rows.excluded]
    included = {row.name.casefold() for row in source_inventory()}
    if len(set(names)) != len(names) or included.intersection(names):
        raise ValidationError("Duplicate or overlapping excluded skills")
    return rows.excluded


def transferred_exclusions() -> tuple[Exclusion, ...]:
    """Reuse the owning supernatural catalog as the authority for excluded skills.

    An excluded skill is only accounted for while another inventory carries it
    with the same page and the same named follow-up issues. Drift there is a
    coverage failure here, not a silent removal from the Basic Set chapter.
    """
    from wayfarer.rules.supernatural import inventory as owning_catalog

    owned = {entry.id: entry for entry in owning_catalog().entries if entry.kind == "skill"}
    rows = exclusions()
    if len(owned) != len(rows):
        raise ValidationError("Excluded skills and their owning catalog differ")
    for row in rows:
        entry = owned.get(f"skill:{row.id}")
        if entry is None or entry.name != row.name or entry.page != row.page:
            raise ValidationError(f"Excluded skill is not carried by its owner: {row.name}")
        if entry.blockers != row.owners:
            raise ValidationError(f"Excluded skill owner drift: {row.name}")
    return rows


def coverage_blockers(profile_id: str) -> tuple[int, ...]:
    """Aggregate item-level owners for certification and authoring reports."""
    if profile_id != PROFILE:
        raise ValidationError("Mundane skill inventory is outside the selected profile")
    entries = inventory()
    return tuple(sorted({issue for entry in entries for issue in entry.followup_issues}))


def inventory() -> tuple[SkillAudit, ...]:
    rows = source_inventory()
    existing = {d.id: d for d in representative_definitions(PROFILE)}
    result = []
    for row in rows:
        identifier = "skill:" + row.id
        definition = existing.get(identifier)
        attributes = {
            "IQ": A.IQ,
            "DX": A.DX,
            "HT": A.HT,
            "ST": A.ST,
            "Will": A.WILL,
            "Per": A.PER,
            "Perception": A.PER,
        }
        spec = (
            SkillSpec(
                attributes[row.attribute],
                row.difficulty,
                f"B{row.page}",
                tuple(
                    SkillDefault(attributes[d.attribute], d.modifier)
                    for d in row.attribute_defaults
                )
                + tuple(SkillDefault(f"skill:{d.target}", d.modifier) for d in row.skill_defaults),
                tuple(SkillPrerequisite(f"skill:{p}") for p in row.prerequisites),
                Specialty(
                    row.specialty.family,
                    row.specialty.name,
                    f"skill:{row.specialty.optional_parent}"
                    if row.specialty.optional_parent
                    else None,
                )
                if row.specialty
                else None,
            )
            if row.attribute is not None and row.difficulty is not None
            else None
        )
        if spec is not None:
            definition = RuleDefinition(
                identifier,
                DefinitionKind.SKILL,
                row.name,
                SOURCE.id,
                None,
                ImplementationStatus.UNSUPPORTED,
                skill=spec,
            )
        if definition is not None:
            definition = replace(definition, status=ImplementationStatus.UNSUPPORTED, hooks=())
        blockers = ["first-printing-delta-audit", *row.blockers]
        if definition is None:
            blockers.append("metadata-audit")
        issues = row.issues
        dispatch: str | None = None
        procedure = RANGED_PROCEDURES.get(identifier)
        if procedure is not None:
            # The binding may only resolve or keep the blockers this inventory
            # recorded, and its numbers must be the recorded ones.
            if set(procedure.resolved) | set(procedure.blockers) != set(row.blockers):
                raise ValidationError(f"Runtime binding disagrees with the inventory: {identifier}")
            if procedure.spec() != spec:
                raise ValidationError(f"Runtime binding changes recorded mechanics: {identifier}")
            if procedure.blockers and not procedure.owners:
                raise ValidationError(f"Transferred row names no owner: {identifier}")
            blockers = [b for b in blockers if b not in procedure.resolved]
            issues = tuple(dict.fromkeys(issues + (RANGED_OWNER,) + procedure.owners))
            if procedure.dispatchable:
                definition = procedure.definition()
                dispatch = "combat.ranged-attack"
        result.append(
            SkillAudit(
                identifier,
                row.name,
                definition.skill.reference if definition and definition.skill else f"B{row.page}",
                definition,
                tuple(blockers),
                issues,
                row.specialty_required,
                row.tl_required,
                bound=procedure is not None and procedure.implemented,
                dispatch=dispatch,
            )
        )
    return tuple(result)


def candidate_package() -> RulesPackage:
    """Separate immutable package; unsupported entries cannot activate campaigns."""
    return RulesPackage(
        "package:gurps-mundane-skill-candidates",
        "0.3.0",
        "gurps-4e-2004",
        (SOURCE,),
        tuple(
            RuleDefinition(
                entry.id,
                DefinitionKind.SKILL,
                entry.name,
                SOURCE.id,
                None,
                ImplementationStatus.UNSUPPORTED,
                skill=entry.definition.skill if entry.definition else None,
            )
            for entry in inventory()
        ),
    )


def validate_inventory(entries: tuple[SkillAudit, ...]) -> None:
    identifiers = {e.id for e in entries}
    if len(identifiers) != len(entries):
        raise ValidationError("Duplicate skill inventory ID")
    for entry in entries:
        if not entry.reference or not entry.followup_issues or not entry.blockers:
            raise ValidationError("Provisional inventory requires references and explicit blockers")
        if entry.definition and entry.definition.skill:
            if entry.definition.id != entry.id:
                raise ValidationError(f"Mismatched skill definition ID for {entry.id}")
            implemented = entry.definition.status is ImplementationStatus.IMPLEMENTED
            if implemented != (entry.dispatch is not None):
                raise ValidationError(f"Only a dispatched row may be implemented: {entry.id}")
            if entry.dispatch is not None and (
                not entry.bound or entry.dispatch not in entry.definition.hooks
            ):
                raise ValidationError(f"Dispatched row must carry its hook: {entry.id}")
            if not implemented and entry.definition.status is not ImplementationStatus.UNSUPPORTED:
                raise ValidationError(f"Provisional definition must be unsupported: {entry.id}")
            spec = entry.definition.skill
            allowed_targets = identifiers | {a.value for a in A}
            if any(d.target not in allowed_targets or d.target == entry.id for d in spec.defaults):
                raise ValidationError(f"Invalid default references for {entry.id}")
            references = tuple(d.target for d in spec.defaults if d.target.startswith("skill:"))
            references += tuple(p.target for p in spec.prerequisites)
            if spec.technique:
                references += (spec.technique.parent,)
            if spec.specialty and spec.specialty.optional_parent:
                references += (spec.specialty.optional_parent,)
            if spec.specialty:
                references += (f"skill:{spec.specialty.family}",)
            if not set(references) <= identifiers:
                raise ValidationError(f"Unresolved skill references for {entry.id}")
            if any(p.target == entry.id or p.minimum < 1 for p in spec.prerequisites):
                raise ValidationError(f"Invalid prerequisite references for {entry.id}")
        if not entry.structural_classes:
            raise ValidationError(f"Unclassified inventory row: {entry.id}")
        if OWNER not in entry.followup_issues:
            raise ValidationError(f"Inventory row leaves this audit unowned: {entry.id}")
    sampled = {structural for entry in entries for structural in entry.structural_classes}
    missing = sorted(set(StructuralClass) - sampled)
    if missing:
        raise ValidationError(f"Structural classes are unsampled: {', '.join(missing)}")


def require_available(identifier: str) -> RuleDefinition:
    entry = next((e for e in inventory() if e.id == identifier), None)
    if entry is None:
        raise ValidationError(f"Skill not in the declared source inventory: {identifier}")
    if not entry.available or entry.definition is None:
        raise ValidationError(f"Skill unavailable: {identifier}: {', '.join(entry.blockers)}")
    return entry.definition


def audit_report() -> dict[str, object]:
    entries = inventory()
    validate_inventory(entries)
    excluded = transferred_exclusions()
    return {
        "profile": PROFILE,
        "source_id": SOURCE.id,
        "baseline": "2004 first printing; errata 2007-01-26; verification pending",
        "inventory_completeness": "indexed-skill-families; specialty expansions explicitly blocked",
        "skills": [
            asdict(entry)
            | {
                "implementation": entry.implementation,
                "structural_classes": [c.value for c in entry.structural_classes],
                "owners": list(entry.owners),
            }
            for entry in entries
        ],
        "excluded": [e.model_dump() for e in excluded],
        "excluded_total": len(excluded),
        "blocker_counts": dict(sorted(Counter(b for e in entries for b in e.blockers).items())),
        "structural_class_counts": {
            structural.value: sum(structural in e.structural_classes for e in entries)
            for structural in StructuralClass
        },
        "implementation_counts": dict(sorted(Counter(e.implementation for e in entries).items())),
        "coverage_blockers": list(coverage_blockers(PROFILE)),
        # Rows whose only recorded issue is this audit have no named mechanics
        # owner yet; that is a visible certification blocker for #122, not silence.
        "runtime_owner_unassigned": sum(not entry.owners for entry in entries),
        "structured": sum(e.definition is not None for e in entries),
        "required_specialties": sum(e.specialty_required for e in entries),
        "technology_level_dependent": sum(e.tl_required for e in entries),
        "total": len(entries),
        "available": sum(e.available for e in entries),
    }
