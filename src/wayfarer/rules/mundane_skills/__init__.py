"""Dedicated provisional Basic Set mundane skill inventory (#112).

Source-indexed metadata remains provisional for the frozen first-printing profile.
Printing deltas and item-level mechanics blockers are explicit audit data.
No existing package pin is changed and unimplemented runtime skills fail closed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
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
from wayfarer.rules.mundane_skills.schema import Exclusion, Exclusions, InventoryRow
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import SkillDefault, SkillPrerequisite, SkillSpec, Specialty

PROFILE = "gurps-basic-set-4e-2004"
SOURCE = source(PROFILE)


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
        result.append(
            SkillAudit(
                identifier,
                row.name,
                definition.skill.reference if definition and definition.skill else f"B{row.page}",
                definition,
                tuple(blockers),
                row.issues,
                row.specialty_required,
                row.tl_required,
            )
        )
    return tuple(result)


def candidate_package() -> RulesPackage:
    """Separate immutable package; unsupported entries cannot activate campaigns."""
    return RulesPackage(
        "package:gurps-mundane-skill-candidates",
        "0.2.0",
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
            if entry.definition.status is not ImplementationStatus.UNSUPPORTED:
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
    excluded = exclusions()
    return {
        "profile": PROFILE,
        "source_id": SOURCE.id,
        "baseline": "2004 first printing; errata 2007-01-26; verification pending",
        "inventory_completeness": "indexed-skill-families; specialty expansions explicitly blocked",
        "skills": [asdict(entry) for entry in entries],
        "excluded": [e.model_dump() for e in excluded],
        "excluded_total": len(excluded),
        "blocker_counts": dict(sorted(Counter(b for e in entries for b in e.blockers).items())),
        "structured": sum(e.definition is not None for e in entries),
        "required_specialties": sum(e.specialty_required for e in entries),
        "technology_level_dependent": sum(e.tl_required for e in entries),
        "total": len(entries),
        "available": sum(e.available for e in entries),
    }
