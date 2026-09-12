"""Item-level equipment audit integrity (#180); no source-completeness claim is made here."""

from pathlib import Path

import pytest

from wayfarer.errors import ValidationError
from wayfarer.simulation.basic_equipment import BASIC_EQUIPMENT, ULTRATECH_INDEX
from wayfarer.simulation.equipment_audit import (
    AUDITED_MODELS,
    PINNED_PACKAGES,
    FieldProvenance,
    Footnote,
    Section,
    audit_report,
    binding_status,
    catalog_entries,
    ledger,
    require_supported,
    rows,
    supported_equipment,
    unregistered_rows,
    unregistered_sources,
    validate,
    validate_selection,
)
from wayfarer.simulation.gurps_equipment import LITE_EQUIPMENT
from wayfarer.simulation.hit_locations import wound_factor

ROOT = Path(__file__).resolve().parents[1]
BASIC = "gurps-basic-set-4e-2004"
LITE = "gurps-lite-4e-2004"


def test_selected_row_provenance_anchors() -> None:
    """Every audited row carries the same third-printing provenance and a page in scope."""
    entries = catalog_entries()
    assert len(entries) == len(BASIC_EQUIPMENT.entries) + len(ULTRATECH_INDEX) == 137
    for entry in entries.values():
        provenance = entry.provenance
        assert provenance.source_id == "sjg:gurps-basic-set-4e-2004"
        assert provenance.edition == "Fourth Edition, third printing (2008)"
        assert provenance.errata.startswith("Third-printing text")
        assert set(provenance.pages) <= {271, 272, 273, 274, 275, 276, 278, 279, 280, 283, 287, 288}
    pages = {entry.definition_id: entry.provenance.pages for entry in entries.values()}
    assert pages["equipment:broadsword"] == (271,)
    assert pages["equipment:leather-armor"] == (283,)
    assert pages["equipment:laptop"] == (288,)
    assert pages["equipment:laser-pistol"] == (280,)
    assert pages["equipment:longbow"] == (275,)
    assert pages["equipment:bolt"] == (276,)
    assert pages["equipment:medium-shield"] == (287,)
    assert pages["equipment:cartridge-rifle-45"] == (279,)


def test_tight_beam_burning_reaches_eyes_and_vitals() -> None:
    """The tight-beam damage flag is the only route for burning damage to those locations."""
    assert wound_factor("left-eye", "burn", tight_beam=True) == (4, 1)
    assert wound_factor("vitals", "burn", tight_beam=True) == (2, 1)
    for location in ("left-eye", "vitals"):
        with pytest.raises(ValidationError, match="tight-beam"):
            wound_factor(location, "burn", tight_beam=False)
    assert wound_factor("torso", "burn", tight_beam=False) == (1, 1)


def test_selected_table_inventory_is_exhaustive_with_explicit_omissions() -> None:
    current = ledger()
    claimed = [row for section in current.sections for row in section.selected]
    assert sorted(claimed) == sorted(name.removeprefix("equipment:") for name in catalog_entries())
    assert len(claimed) == len(set(claimed))
    omitted = [section for section in current.sections if section.status == "omitted"]
    assert omitted and all(section.omitted_rows != "none" for section in current.sections)
    assert not any(section.status == "audited" for section in current.sections)
    # An omitted group names row content, never an empty placeholder.
    assert all(len(section.omitted_rows) > 40 for section in omitted)


def test_every_catalog_blocker_has_an_unsupported_footnote() -> None:
    footnotes = {footnote.id: footnote for footnote in ledger().footnotes}
    declared = {m for entry in catalog_entries().values() for m in entry.unsupported_mechanics}
    assert declared
    for mechanic in declared:
        assert footnotes[mechanic].disposition == "unsupported"
        assert footnotes[mechanic].owner_issue is not None
    implemented = [f for f in ledger().footnotes if f.disposition == "implemented"]
    assert implemented and all(f.capability_id for f in implemented)
    # An implemented behaviour without an executable case still names its owner.
    for footnote in implemented:
        assert bool(footnote.tests) != (footnote.evidence_gap is not None)
        assert (footnote.evidence_gap is None) == (footnote.owner_issue is None)


def test_field_provenance_tracks_the_whole_equipment_schema() -> None:
    expected = {f"{m.__name__}.{n}" for m in AUDITED_MODELS for n in m.model_fields}
    records = {record.id: record for record in ledger().fields}
    assert set(records) == expected
    assert all(record.status == "pending" for record in records.values())
    assert records["EquipmentProfile.weight_millipounds"].unit == "thousandths of a pound"
    assert records["EquipmentProfile.container_capacity_millipounds"].unit == (
        "thousandths of a pound"
    )
    assert records["EquipmentProfile.price"].unit == "dollars"
    # Shield skill/profile, melee parry/hand-count and ranged bulk now have
    # direct selected-table cases.
    assert records["Shield.skill_id"].gap is None
    assert records["EquipmentProfile.shield"].gap is None
    assert records["RangedMode.bulk"].gap is None
    assert records["RangedMode.chamber_capacity"].gap is None
    assert records["Parry.modifier"].gap is None
    assert records["MeleeMode.hands"].gap is None


def test_selection_rejects_unsupported_and_unknown_equipment() -> None:
    allowed = supported_equipment(BASIC)
    assert "equipment:broadsword" in allowed
    assert "equipment:laser-pistol" not in allowed
    assert validate_selection(BASIC, ("equipment:broadsword",)) == {"equipment:broadsword"}
    with pytest.raises(ValidationError, match="power-cell-charges"):
        validate_selection(BASIC, ("equipment:broadsword", "equipment:laser-pistol"))
    with pytest.raises(ValidationError, match="outside the selected-table audit"):
        validate_selection(BASIC, ("equipment:invented-blade",))
    assert require_supported("equipment:broadsword").definition_id == "equipment:broadsword"
    with pytest.raises(ValidationError, match="battery-and-computer-operation"):
        require_supported("equipment:laptop")


def test_lite_equipment_gaps_are_recorded_separately_and_block_lite_selection() -> None:
    gaps = ledger().lite_gaps
    assert {gap.owner_issue for gap in gaps} == {121, 191}
    with pytest.raises(ValidationError, match="lite-weapon-table"):
        supported_equipment(LITE)
    with pytest.raises(ValidationError, match="Unknown equipment audit profile"):
        supported_equipment("gurps-invented-2099")
    lite_rows = [row for row in rows() if row.scope == "lite-equipment-gaps"]
    assert len(lite_rows) == len(gaps)
    assert all(row.required_profiles == (LITE,) for row in lite_rows)


def test_audited_catalogs_do_not_bind_to_pinned_packages() -> None:
    """No registered package declares equipment, so no audited row resolves outside tests."""
    for catalog in (LITE_EQUIPMENT, BASIC_EQUIPMENT):
        assert binding_status(catalog) == "unbound"
        assert unregistered_rows(catalog) == tuple(
            sorted(entry.definition_id for entry in catalog.entries)
        )
        with pytest.raises(ValidationError):
            catalog.bind(PINNED_PACKAGES)
    assert unregistered_sources(BASIC_EQUIPMENT) == ("sjg:gurps-basic-set-4e-2004",)
    assert unregistered_sources(LITE_EQUIPMENT) == ()
    recorded = ledger().bindings
    assert {binding.catalog for binding in recorded} == {BASIC, LITE}
    assert all(binding.status == "unbound" for binding in recorded)
    lite = next(binding for binding in recorded if binding.catalog == LITE)
    assert lite.owner_issue == 121


def test_audit_report_names_blockers_without_claiming_completeness() -> None:
    report = audit_report(ROOT)
    assert report["audit_complete"] is False
    assert report["selected_rows"] == 137
    assert report["supported_rows"] == 83
    assert report["sections_audited"] == 0
    assert isinstance(report["blockers"], list) and report["blockers"]
    assert report["footnotes_without_evidence"] == []
    uncovered = report["fields_uncovered"]
    assert isinstance(uncovered, list) and uncovered
    assert report["unbound_catalogs"] == [BASIC, LITE]
    scopes = {row.scope for row in rows()}
    assert scopes == {
        "equipment-sections",
        "equipment-footnotes",
        "equipment-field-provenance",
        "equipment-package-binding",
        "lite-equipment-gaps",
    }
    covered = [row for row in rows() if not row.blockers]
    assert covered and all(row.implementation in ("implemented", "partial") for row in covered)
    field_blockers = [
        row for row in rows() if row.scope == "equipment-field-provenance" and row.blockers
    ]
    assert len(field_blockers) == len(uncovered)


def test_schema_drift_and_missing_evidence_are_rejected() -> None:
    current = ledger()
    with pytest.raises(ValidationError, match="fields and declared provenance"):
        validate(current.model_copy(update={"fields": current.fields[1:]}))

    section = current.sections[0].model_copy(update={"selected": ("invented-blade",)})
    with pytest.raises(ValidationError, match="unregistered row"):
        validate(current.model_copy(update={"sections": (section, *current.sections[1:])}))

    trimmed = current.model_copy(update={"sections": current.sections[1:]})
    with pytest.raises(ValidationError, match="not exhaustive"):
        validate(trimmed)

    unblocked = tuple(s.model_copy(update={"mechanics": ()}) for s in current.sections)
    footnotes = tuple(f for f in current.footnotes if f.id != "power-cell-charges")
    with pytest.raises(ValidationError, match="no unsupported disposition"):
        validate(current.model_copy(update={"sections": unblocked, "footnotes": footnotes}))

    stale = current.bindings[0].model_copy(update={"status": "bound"})
    with pytest.raises(ValidationError, match="binding is stale"):
        validate(current.model_copy(update={"bindings": (stale, *current.bindings[1:])}))

    bound = current.fields[0].model_copy(
        update={"tests": ("tests/test_equipment_audit.py::test_invented",), "gap": None}
    )
    with pytest.raises(ValidationError, match="Missing equipment audit test"):
        validate(current.model_copy(update={"fields": (bound, *current.fields[1:])}), ROOT)


def test_evidence_records_cannot_hide_a_missing_binding() -> None:
    with pytest.raises(ValueError, match="records evidence or its exact absence"):
        Footnote(
            id="invented",
            reference="B271",
            anchor="inspected",
            behaviour="Invented behaviour",
            disposition="implemented",
            capability_id="gurps.combat.melee_attack",
        )
    with pytest.raises(ValueError, match="no support evidence"):
        Footnote(
            id="invented",
            reference="B271",
            anchor="inspected",
            behaviour="Invented behaviour",
            disposition="unsupported",
            owner_issue=180,
            tests=("tests/test_equipment_audit.py::test_selected_row_provenance_anchors",),
        )
    with pytest.raises(ValueError, match="executable coverage or an explicit gap"):
        FieldProvenance(
            id="Armor.dr",
            unit="damage resistance",
            reference="B283",
            anchor="inspected",
            status="pending",
        )
    with pytest.raises(ValueError, match="independent review evidence"):
        FieldProvenance(
            id="Armor.dr",
            unit="damage resistance",
            reference="B283",
            anchor="inspected",
            status="compared",
            tests=("tests/test_equipment_audit.py::test_selected_row_provenance_anchors",),
        )


def test_section_records_cannot_hide_an_incomplete_audit() -> None:
    with pytest.raises(ValueError, match="omitted section audits no rows"):
        Section(
            id="invented",
            reference="B271",
            anchor="inspected",
            source_id="sjg:basic-set-characters-4e-2004",
            owner_issue=180,
            status="omitted",
            selected=("broadsword",),
            omitted_rows="Everything else.",
        )
    with pytest.raises(ValueError, match="must describe its omitted rows"):
        Section(
            id="invented",
            reference="B271",
            anchor="inspected",
            source_id="sjg:basic-set-characters-4e-2004",
            owner_issue=180,
            status="partial",
            selected=("broadsword",),
            omitted_rows="none",
        )
    with pytest.raises(ValueError, match="inspected anchor and omits nothing"):
        Section(
            id="invented",
            reference="B264-289",
            anchor="range-only",
            source_id="sjg:basic-set-characters-4e-2004",
            owner_issue=180,
            status="audited",
            selected=("broadsword",),
            omitted_rows="none",
        )
    with pytest.raises(ValueError, match="Duplicate selected equipment row"):
        Section(
            id="invented",
            reference="B271",
            anchor="inspected",
            source_id="sjg:basic-set-characters-4e-2004",
            owner_issue=180,
            status="partial",
            selected=("broadsword", "broadsword"),
            omitted_rows="Everything else.",
        )
    with pytest.raises(ValueError, match="declared capability"):
        Footnote(
            id="invented",
            reference="B271",
            anchor="inspected",
            behaviour="Invented behaviour",
            disposition="implemented",
            capability_id="gurps.invented",
            tests=("tests/test_equipment_audit.py::test_selected_row_provenance_anchors",),
        )
    with pytest.raises(ValueError, match="needs an owning issue"):
        Footnote(
            id="invented",
            reference="B271",
            anchor="inspected",
            behaviour="Invented behaviour",
            disposition="implemented",
            capability_id="gurps.combat.melee_attack",
            evidence_gap="No case exists",
        )
    with pytest.raises(ValueError, match="cannot claim a source comparison"):
        FieldProvenance(
            id="Armor.dr",
            unit="damage resistance",
            reference="B283",
            anchor="inspected",
            status="compared",
            reviewer="reviewer",
            evidence="evidence",
            gap="No case exists",
        )


def test_further_ledger_drift_is_rejected() -> None:
    current = ledger()
    section = current.sections[0].model_copy(update={"source_id": "sjg:invented"})
    with pytest.raises(ValidationError, match="Unknown equipment section source"):
        validate(current.model_copy(update={"sections": (section, *current.sections[1:])}))

    blocking = current.sections[0].model_copy(update={"mechanics": ("invented-behaviour",)})
    with pytest.raises(ValidationError, match="undeclared behaviour"):
        validate(current.model_copy(update={"sections": (blocking, *current.sections[1:])}))

    duplicated = current.sections[1].model_copy(update={"selected": current.sections[0].selected})
    with pytest.raises(ValidationError, match="belongs to exactly one section"):
        validate(
            current.model_copy(
                update={"sections": (current.sections[0], duplicated, *current.sections[2:])}
            )
        )

    clashed = current.lite_gaps[0].model_copy(update={"id": current.lite_gaps[1].id})
    with pytest.raises(ValidationError, match="Duplicate equipment audit identifier"):
        validate(current.model_copy(update={"lite_gaps": (clashed, *current.lite_gaps[1:])}))

    with pytest.raises(ValidationError, match="needs a binding record"):
        validate(current.model_copy(update={"bindings": current.bindings[:1]}))
