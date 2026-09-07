"""Dedicated provisional Basic Set mundane skill inventory (#112).

Inventory and metadata are reconstructed from model knowledge under owner
approval. Exact first-printing page/default/specialty verification is pending.
No existing package pin is changed and unimplemented runtime skills fail closed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
)
from wayfarer.rules.gurps_characters import source
from wayfarer.rules.gurps_skills import definitions as representative_definitions
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D
from wayfarer.rules.skill_types import SkillDefault, SkillSpec

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
    provenance: str = (
        "Characters Fourth Edition, third printing; first-printing delta audit pending"
    )

    @property
    def available(self) -> bool:
        return not self.blockers


def inventory() -> tuple[SkillAudit, ...]:
    rows = json.loads(Path(__file__).with_name("inventory.json").read_text())
    existing = {d.id: d for d in representative_definitions(PROFILE)}
    result = []
    for row in rows:
        identifier = "skill:" + row["id"]
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
                attributes[row["attribute"]],
                D(row["difficulty"]),
                f"B{row['page']}",
                tuple(
                    SkillDefault(attributes[d["attribute"]], d["modifier"])
                    for d in row.get("attribute_defaults", [])
                ),
            )
            if "attribute" in row
            else None
        )
        if spec is not None:
            definition = RuleDefinition(
                identifier,
                DefinitionKind.SKILL,
                row["name"],
                SOURCE.id,
                None,
                ImplementationStatus.UNSUPPORTED,
                skill=spec,
            )
        blockers = ["first-printing-delta-audit", *row["blockers"]]
        if definition is None:
            blockers.append("metadata-audit")
        result.append(
            SkillAudit(
                identifier,
                row["name"],
                definition.skill.reference
                if definition and definition.skill
                else f"B{row['page']}",
                definition,
                tuple(blockers),
                tuple(row["issues"]),
            )
        )
    return tuple(result)


def candidate_package() -> RulesPackage:
    """Separate immutable package; unsupported entries cannot activate campaigns."""
    return RulesPackage(
        "package:gurps-mundane-skill-candidates",
        "0.1.0",
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
            spec = entry.definition.skill
            references = tuple(d.target for d in spec.defaults if d.target.startswith("skill:"))
            references += tuple(p.target for p in spec.prerequisites)
            if spec.technique:
                references += (spec.technique.parent,)
            if spec.specialty and spec.specialty.optional_parent:
                references += (spec.specialty.optional_parent,)
            if not set(references) <= identifiers:
                raise ValidationError(f"Unresolved skill references for {entry.id}")


def require_available(identifier: str) -> RuleDefinition:
    entry = next((e for e in inventory() if e.id == identifier), None)
    if entry is None:
        raise ValidationError(f"Skill not in the declared source inventory: {identifier}")
    if entry.blockers or entry.definition is None:
        raise ValidationError(f"Skill unavailable: {identifier}: {', '.join(entry.blockers)}")
    return entry.definition


def audit_report() -> dict[str, object]:
    entries = inventory()
    validate_inventory(entries)
    return {
        "profile": PROFILE,
        "source_id": SOURCE.id,
        "baseline": "2004 first printing; errata 2007-01-26; verification pending",
        "inventory_completeness": "indexed-skill-families; specialty expansions explicitly blocked",
        "skills": [asdict(entry) for entry in entries],
        "total": len(entries),
        "available": sum(e.available for e in entries),
    }
