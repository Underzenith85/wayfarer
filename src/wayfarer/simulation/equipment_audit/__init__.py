"""Item-level equipment reconciliation for the selected GURPS equipment tables (#180).

This module holds evidence metadata only. It contains no rules prose, no invented
numeric rows and no second mechanics engine. It records which selected table rows
the repository actually recorded, which row groups are explicitly omitted, how each
special-gear behaviour is dispositioned, and which schema field carries which unit
and source anchor. Completeness is never inferred from a row count, and an anchor
recorded as ``range-only`` is a page range that nobody has reconciled item by item.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.rules.catalog import DefinitionKind, RulesPackage
from wayfarer.rules.conformance import CAPABILITIES, PROFILES
from wayfarer.rules.entangle_types import EntangleSpec
from wayfarer.rules.explosion_types import ExplosionSpec
from wayfarer.rules.firearm_types import FirearmSpec
from wayfarer.rules.gurps_equipment_manifest import EQUIPMENT_SKILL_IDS, SUPPORTED_EQUIPMENT_IDS
from wayfarer.rules.launcher_types import LauncherSpec
from wayfarer.rules.mount_types import MountSpec
from wayfarer.rules.profiles import (
    GURPS_CAMPAIGNS_PACKAGE,
    GURPS_CHARACTERS_PACKAGE,
    GURPS_LITE_PACKAGE,
)
from wayfarer.rules.readiness_types import ProjectileReadiness
from wayfarer.rules.spray_types import SprayerSpec
from wayfarer.simulation.basic_equipment import BASIC_EQUIPMENT, ULTRATECH_INDEX
from wayfarer.simulation.gurps_equipment import (
    LITE_EQUIPMENT,
    Armor,
    Damage,
    EquipmentCatalog,
    EquipmentProfile,
    MeleeMode,
    Parry,
    Provenance,
    RangedMode,
    RatedStrength,
    RocketAcceleration,
    Shield,
    SmartgunSpec,
)

LEDGER_PATH = Path(__file__).with_name("ledger.json")

EQUIPMENT_ISSUE = 180
"""Owner of the item-level table audit and special gear behaviour."""

PROFILE_FIELD_ISSUE = 101
"""Origin of the weapon/armor profile field verification carried forward into #180."""

AUDITED_MODELS = (
    Provenance,
    Damage,
    Parry,
    MeleeMode,
    RangedMode,
    RatedStrength,
    RocketAcceleration,
    SmartgunSpec,
    FirearmSpec,
    ExplosionSpec,
    ProjectileReadiness,
    EntangleSpec,
    MountSpec,
    SprayerSpec,
    LauncherSpec,
    Armor,
    Shield,
    EquipmentProfile,
)
"""Every equipment schema model whose fields require a declared unit and source anchor."""

PINNED_PACKAGES: tuple[RulesPackage, ...] = (
    GURPS_CHARACTERS_PACKAGE,
    GURPS_CAMPAIGNS_PACKAGE,
)
"""The registered Basic Set packages, never a test double."""

LITE_PINNED_PACKAGES: tuple[RulesPackage, ...] = (GURPS_LITE_PACKAGE,)
"""The registered Lite package, kept separate from overlapping Basic definitions."""

CATALOGS: tuple[EquipmentCatalog, ...] = (LITE_EQUIPMENT, BASIC_EQUIPMENT)
"""Both declared equipment catalogs; the ultra-tech index is deliberately not one."""


def packages_for(catalog: EquipmentCatalog) -> tuple[RulesPackage, ...]:
    """Return only the immutable package set selected by this catalog profile."""
    return LITE_PINNED_PACKAGES if catalog.profile_id == "gurps-lite-4e-2004" else PINNED_PACKAGES


Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]
Text = Annotated[str, Field(min_length=1)]
Issue = Annotated[int, Field(gt=0)]
TestBinding = Annotated[str, Field(pattern=r"^tests/[A-Za-z0-9_/]+\.py::[A-Za-z0-9_]+$")]
Anchor = Literal["inspected", "range-only"]


class Section(Record):
    """One selected-table row group: what was audited and what is explicitly omitted."""

    id: Identifier
    reference: Text
    anchor: Anchor
    source_id: Text
    owner_issue: Issue
    status: Literal["audited", "reconciled", "partial", "omitted"]
    selected: tuple[Identifier, ...] = ()
    omitted_rows: Text
    # The special behaviours the omitted rows exercise; the unresolved ones block.
    mechanics: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(set(self.selected)) != len(self.selected):
            raise ValueError("Duplicate selected equipment row")
        if self.status == "omitted" and self.selected:
            raise ValueError("An omitted section audits no rows")
        if self.status == "partial" and not self.selected:
            raise ValueError("A partial section audits at least one selected row")
        if self.status == "audited":
            if self.mechanics or self.anchor != "inspected" or self.omitted_rows != "none":
                raise ValueError("A complete section has an inspected anchor and omits nothing")
        elif self.status == "reconciled":
            if self.anchor != "inspected" or self.omitted_rows == "none":
                raise ValueError(
                    "A reconciled section has an inspected anchor and explicit omissions"
                )
        elif self.omitted_rows == "none":
            raise ValueError("An incomplete section must describe its omitted rows")
        return self


class Footnote(Record):
    """One special-gear behaviour, dispositioned as implemented or explicitly unsupported.

    An implemented behaviour without an executable binding records the missing
    evidence and keeps its owning issue; it is never counted as complete.
    """

    id: Identifier
    reference: Text
    anchor: Anchor
    behaviour: Text
    disposition: Literal["implemented", "unsupported"]
    capability_id: Text | None = None
    owner_issue: Issue | None = None
    tests: tuple[TestBinding, ...] = ()
    evidence_gap: Text | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.disposition == "implemented":
            if self.capability_id not in CAPABILITIES:
                raise ValueError(f"Implemented behaviour needs a declared capability: {self.id}")
            if bool(self.tests) == (self.evidence_gap is not None):
                raise ValueError("Implemented behaviour records evidence or its exact absence")
            if (self.evidence_gap is not None) != (self.owner_issue is not None):
                raise ValueError("Missing behaviour evidence needs an owning issue")
        elif (
            self.capability_id is not None
            or self.owner_issue is None
            or self.tests
            or self.evidence_gap is not None
        ):
            raise ValueError("Unsupported behaviour needs an owning issue and no support evidence")
        return self


class FieldProvenance(Record):
    """Field-level unit and source anchor for the #101 weapon/armor profile carry-forward."""

    id: Annotated[str, Field(pattern=r"^[A-Za-z]+\.[a-z][a-z0-9_]*$")]
    unit: Text
    reference: Text
    anchor: Anchor
    status: Literal["pending", "compared", "reviewed"]
    reviewer: Text | None = None
    evidence: Text | None = None
    tests: tuple[TestBinding, ...] = ()
    gap: Text | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if bool(self.tests) == (self.gap is not None):
            raise ValueError("A field records either executable coverage or an explicit gap")
        if self.status != "pending" and not (self.reviewer and self.evidence):
            raise ValueError("A compared or reviewed field needs independent review evidence")
        if self.status != "pending" and self.gap is not None:
            raise ValueError("An uncovered field cannot claim a source comparison")
        return self


class Binding(Record):
    """Whether an audited catalog actually resolves against the pinned packages."""

    id: Identifier
    catalog: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
    status: Literal["bound", "unbound"]
    detail: Text
    owner_issue: Issue
    tests: Annotated[tuple[TestBinding, ...], Field(min_length=1)]


class LiteGap(Record):
    """A Lite-required gap recorded separately so #121 cannot pass on deferred Basic work."""

    id: Identifier
    reference: Text
    owner_issue: Issue
    detail: Text


class Ledger(Record):
    schema_version: Literal[1]
    profile_id: Literal["gurps-basic-set-4e-2004"]
    lite_profile_id: Literal["gurps-lite-4e-2004"]
    printing: Text
    baseline_note: Text
    sections: Annotated[tuple[Section, ...], Field(min_length=1)]
    footnotes: Annotated[tuple[Footnote, ...], Field(min_length=1)]
    fields: Annotated[tuple[FieldProvenance, ...], Field(min_length=1)]
    bindings: Annotated[tuple[Binding, ...], Field(min_length=1)]
    lite_gaps: Annotated[tuple[LiteGap, ...], Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One certification-visible equipment audit row consumed by the source audit."""

    id: str
    reference: str
    owner: int
    implementation: Literal["implemented", "partial", "unsupported", "omitted"]
    scope: str
    required_profiles: tuple[str, ...]
    blockers: tuple[int, ...]


@cache
def ledger() -> Ledger:
    return Ledger.model_validate_json(LEDGER_PATH.read_text())


def catalog_entries() -> dict[str, EquipmentProfile]:
    """Every entry the repository claims from the selected tables, active or blocked."""
    return {entry.definition_id: entry for entry in (*BASIC_EQUIPMENT.entries, *ULTRATECH_INDEX)}


def unregistered_rows(catalog: EquipmentCatalog) -> tuple[str, ...]:
    """Supported catalog rows with no equipment definition in a pinned package."""
    registered = {
        definition.id
        for package in packages_for(catalog)
        for definition in package.definitions
        if definition.kind is DefinitionKind.EQUIPMENT
    }
    return tuple(
        sorted(
            e.definition_id
            for e in catalog.entries
            if not e.unsupported_mechanics and e.definition_id not in registered
        )
    )


def unregistered_sources(catalog: EquipmentCatalog) -> tuple[str, ...]:
    """Supported-row source IDs that no pinned package declares."""
    declared = {source.id for package in packages_for(catalog) for source in package.sources}
    return tuple(
        sorted(
            {e.provenance.source_id for e in catalog.entries if not e.unsupported_mechanics}
            - declared
        )
    )


def binding_status(catalog: EquipmentCatalog) -> Literal["bound", "unbound"]:
    """A catalog is bound only when every row and every cited source is registered."""
    if unregistered_rows(catalog) or unregistered_sources(catalog):
        return "unbound"
    try:
        catalog.bind(packages_for(catalog))
    except ValidationError:
        return "unbound"
    return "bound"


def _row_id(definition_id: str) -> str:
    prefix, _, suffix = definition_id.partition(":")
    if prefix != "equipment" or not suffix:
        raise ValidationError(f"Selected rows use equipment definition IDs: {definition_id}")
    return suffix


def validate(current: Ledger, root: Path | None = None) -> None:
    """Reject drift between the ledger, the equipment schema and the pinned catalog."""
    for records in (current.sections, current.footnotes, current.fields, current.lite_gaps):
        identifiers = [record.id for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("Duplicate equipment audit identifier")

    entries = catalog_entries()
    supported = {
        entry.definition_id for entry in BASIC_EQUIPMENT.entries if not entry.unsupported_mechanics
    }
    if supported != set(SUPPORTED_EQUIPMENT_IDS):
        raise ValidationError("Supported equipment catalog and package manifest have drifted")
    skill_ids: set[str] = set()
    for entry in BASIC_EQUIPMENT.entries:
        if entry.unsupported_mechanics:
            continue
        skill_ids.update(mode.skill_id for mode in entry.modes)
        for mode in entry.modes:
            if isinstance(mode, RangedMode):
                if mode.readiness is not None and mode.readiness.fast_draw_skill_id is not None:
                    skill_ids.add(mode.readiness.fast_draw_skill_id)
                if mode.firearm is not None and mode.firearm.armoury_skill_id is not None:
                    skill_ids.add(mode.firearm.armoury_skill_id)
        if entry.shield is not None:
            skill_ids.add(entry.shield.skill_id)
    if skill_ids != set(EQUIPMENT_SKILL_IDS):
        raise ValidationError("Equipment skill references and package manifest have drifted")
    footnotes = {footnote.id: footnote for footnote in current.footnotes}
    profile_sources = {source for target in PROFILES.values() for source in target.source_ids}
    claimed: list[str] = []
    for section in current.sections:
        if section.source_id not in profile_sources:
            raise ValidationError(f"Unknown equipment section source: {section.id}")
        if not set(section.mechanics) <= set(footnotes):
            raise ValidationError(f"Section {section.id} blocks on an undeclared behaviour")
        for row in section.selected:
            if "equipment:" + row not in entries:
                raise ValidationError(f"Section {section.id} selects an unregistered row: {row}")
            claimed.append(row)
    if len(claimed) != len(set(claimed)):
        raise ValidationError("A selected equipment row belongs to exactly one section")
    if set(claimed) != {_row_id(identifier) for identifier in entries}:
        raise ValidationError("Selected-table inventory is not exhaustive over the pinned catalog")

    for mechanic in sorted({m for entry in entries.values() for m in entry.unsupported_mechanics}):
        footnote = footnotes.get(mechanic)
        if footnote is None or footnote.disposition != "unsupported":
            raise ValidationError(f"Catalog blocker has no unsupported disposition: {mechanic}")

    catalogs = {catalog.profile_id: catalog for catalog in CATALOGS}
    if {binding.catalog for binding in current.bindings} != set(catalogs):
        raise ValidationError("Every declared equipment catalog needs a binding record")
    for binding in current.bindings:
        if binding.status != binding_status(catalogs[binding.catalog]):
            raise ValidationError(f"Recorded package binding is stale: {binding.id}")

    expected = {
        f"{model.__name__}.{name}" for model in AUDITED_MODELS for name in model.model_fields
    }
    if {record.id for record in current.fields} != expected:
        raise ValidationError("Equipment schema fields and declared provenance have drifted")

    if root is not None:
        cases = [case for record in current.footnotes for case in record.tests]
        cases.extend(case for record in current.fields for case in record.tests)
        cases.extend(case for record in current.bindings for case in record.tests)
        for case in cases:
            filename, _, function = case.partition("::")
            path = root / filename
            if not path.is_file() or f"def {function}(" not in path.read_text():
                raise ValidationError(f"Missing equipment audit test: {case}")


def rows() -> tuple[AuditRow, ...]:
    """Certification-visible rows: unresolved mechanics stay unsupported, not hidden."""
    current = ledger()
    footnotes = {footnote.id: footnote for footnote in current.footnotes}
    result = [
        AuditRow(
            "equipment-section/" + section.id,
            section.reference,
            section.owner_issue,
            "implemented"
            if section.status in ("audited", "reconciled")
            else "omitted"
            if section.status == "omitted"
            else "partial",
            "equipment-sections",
            (current.profile_id,),
            ()
            if section.status in ("audited", "reconciled")
            else tuple(
                sorted(
                    {section.owner_issue}
                    | {
                        issue
                        for mechanic in section.mechanics
                        if (issue := footnotes[mechanic].owner_issue) is not None
                    }
                )
            ),
        )
        for section in current.sections
    ]
    result.extend(
        AuditRow(
            "equipment-footnote/" + footnote.id,
            footnote.reference,
            footnote.owner_issue or EQUIPMENT_ISSUE,
            "implemented" if footnote.disposition == "implemented" else "unsupported",
            "equipment-footnotes",
            (current.profile_id,),
            (),
        )
        for footnote in current.footnotes
    )
    result.extend(
        AuditRow(
            "equipment-field/" + record.id,
            record.reference,
            PROFILE_FIELD_ISSUE,
            "partial" if record.tests else "omitted",
            "equipment-field-provenance",
            (current.profile_id, current.lite_profile_id),
            () if record.gap is None else (PROFILE_FIELD_ISSUE,),
        )
        for record in current.fields
    )
    result.extend(
        AuditRow(
            "equipment-binding/" + binding.id,
            binding.detail,
            binding.owner_issue,
            "implemented" if binding.status == "bound" else "unsupported",
            "equipment-package-binding",
            (binding.catalog,),
            () if binding.status == "bound" else (binding.owner_issue,),
        )
        for binding in current.bindings
    )
    result.extend(
        AuditRow(
            "lite-equipment-gap/" + gap.id,
            gap.reference,
            gap.owner_issue,
            "omitted",
            "lite-equipment-gaps",
            (current.lite_profile_id,),
            (gap.owner_issue,),
        )
        for gap in current.lite_gaps
    )
    return tuple(result)


def lite_gaps() -> tuple[LiteGap, ...]:
    """Lite-required equipment gaps, so #121 cannot pass on deferred Basic Set work."""
    return ledger().lite_gaps


def supported_equipment(profile_id: str) -> frozenset[str]:
    """Definition IDs a scenario or character policy may allow, from audited rows only."""
    current = ledger()
    if profile_id == current.lite_profile_id:
        raise ValidationError(
            "Lite equipment selection is blocked by recorded gaps: "
            + ", ".join(gap.id for gap in current.lite_gaps)
        )
    if profile_id != current.profile_id:
        raise ValidationError(f"Unknown equipment audit profile: {profile_id}")
    return frozenset(
        entry.definition_id
        for entry in catalog_entries().values()
        if not entry.unsupported_mechanics
    )


def require_supported(definition_id: str) -> EquipmentProfile:
    """Reject an entry outside the audit or one carrying an unsupported behaviour."""
    entry = catalog_entries().get(definition_id)
    if entry is None:
        raise ValidationError(f"Equipment outside the selected-table audit: {definition_id}")
    if entry.unsupported_mechanics:
        footnotes = {footnote.id: footnote for footnote in ledger().footnotes}
        detail = "; ".join(
            f"{mechanic} (#{footnotes[mechanic].owner_issue})"
            for mechanic in entry.unsupported_mechanics
        )
        raise ValidationError(f"Equipment has unsupported mechanics: {definition_id}: {detail}")
    return entry


def validate_selection(profile_id: str, requested: Iterable[str]) -> frozenset[str]:
    """Scenario/character gate: every requested equipment ID must be audited and supported."""
    selection = frozenset(requested)
    allowed = supported_equipment(profile_id)
    for definition_id in sorted(selection - allowed):
        require_supported(definition_id)
    return selection


def audit_report(root: Path | None = None) -> dict[str, object]:
    """Emit the complete item-level audit; blockers are named, never summarised away."""
    current = ledger()
    validate(current, root)
    blocking = tuple(row.id for row in rows() if row.blockers)
    owner_blocking = tuple(
        row.id for row in rows() if row.blockers and EQUIPMENT_ISSUE in row.blockers
    )
    return {
        "profile": current.profile_id,
        "lite_profile": current.lite_profile_id,
        "printing": current.printing,
        "baseline": current.baseline_note,
        "selected_rows": len(catalog_entries()),
        "supported_rows": len(supported_equipment(current.profile_id)),
        "sections_total": len(current.sections),
        "sections_audited": sum(s.status == "audited" for s in current.sections),
        "sections_reconciled": sum(s.status == "reconciled" for s in current.sections),
        "sections_omitted": sum(s.status == "omitted" for s in current.sections),
        "footnotes_implemented": sum(f.disposition == "implemented" for f in current.footnotes),
        "footnotes_unsupported": sum(f.disposition == "unsupported" for f in current.footnotes),
        "footnotes_without_evidence": [
            f.id for f in current.footnotes if f.evidence_gap is not None
        ],
        "fields_total": len(current.fields),
        "fields_uncovered": [f.id for f in current.fields if f.gap is not None],
        "unbound_catalogs": [b.catalog for b in current.bindings if b.status == "unbound"],
        "sections": [section.model_dump() for section in current.sections],
        "footnotes": [footnote.model_dump() for footnote in current.footnotes],
        "fields": [record.model_dump() for record in current.fields],
        "bindings": [binding.model_dump() for binding in current.bindings],
        "lite_gaps": [gap.model_dump() for gap in current.lite_gaps],
        "audit_complete": not blocking,
        "workstream_complete": not owner_blocking,
        "workstream_blockers": list(owner_blocking),
        "blockers": list(blocking),
    }
