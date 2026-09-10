"""Catalog identity, structural classes and fail-closed runtime availability."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import ImplementationStatus, RulesCatalog
from wayfarer.rules.mundane_skills import (
    PROFILE,
    StructuralClass,
    audit_report,
    candidate_package,
    coverage_blockers,
    exclusions,
    inventory,
    require_available,
    source_inventory,
    transferred_exclusions,
    validate_inventory,
)
from wayfarer.rules.mundane_skills.schema import Exclusion, InventoryRow
from wayfarer.rules.skill_types import Difficulty


def test_inventory_and_references() -> None:
    entries = inventory()
    validate_inventory(entries)
    ids = {e.id for e in entries}
    assert {
        "skill:accounting",
        "skill:surgery",
        "skill:survival-woodlands",
        "skill:arm-lock-judo",
    } <= ids
    assert len(entries) > 180
    assert audit_report()["available"] == 0
    assert all(e.blockers and e.followup_issues for e in entries)
    RulesCatalog((candidate_package(),))
    assert candidate_package().digest == candidate_package().digest
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_inventory(entries + entries[:1])
    with pytest.raises(ValidationError, match="references"):
        validate_inventory(tuple(e for e in entries if e.id != "skill:karate"))


def test_numeric_metadata_and_structural_classes() -> None:
    entries = {e.id: e for e in inventory()}
    first_aid = entries["skill:first-aid"].definition
    assert first_aid is not None and first_aid.skill is not None
    assert first_aid.skill.difficulty is Difficulty.EASY
    assert first_aid.skill.defaults[0].modifier == -4
    intimidation = entries["skill:intimidation"].definition
    assert intimidation is not None and intimidation.skill is not None
    assert intimidation.skill.attribute == "secondary:will"
    specs = [e.definition.skill for e in entries.values() if e.definition and e.definition.skill]
    assert {s.difficulty for s in specs} == set(Difficulty)
    assert any(s.technique for s in specs)
    assert any(s.specialty and s.specialty.optional_parent for s in specs)
    assert any(s.specialty and s.specialty.optional_parent is None for s in specs)
    changed = replace(first_aid, name="changed")
    package = candidate_package()
    assert replace(package, definitions=(changed,)).digest != package.digest


@pytest.mark.parametrize("id", ["skill:first-aid", "skill:physics", "skill:invented-skill"])
def test_model_cannot_turn_inventory_into_available_mechanics(id: str) -> None:
    with pytest.raises(ValidationError):
        require_available(id)


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        (
            "accounting",
            {
                "attribute:iq": -6,
                "skill:finance": -4,
                "skill:mathematics-statistics": -5,
                "skill:merchant": -5,
            },
        ),
        ("acting", {"attribute:iq": -5, "skill:performance": -2, "skill:public-speaking": -5}),
        (
            "first-aid",
            {
                "attribute:iq": -4,
                "skill:esoteric-medicine": 0,
                "skill:physician": 0,
                "skill:veterinary": -4,
            },
        ),
        (
            "surgery",
            {
                "skill:first-aid": -12,
                "skill:physician": -5,
                "skill:physiology": -8,
                "skill:veterinary": -5,
            },
        ),
    ],
)
def test_source_indexed_default_alternatives(identifier: str, expected: dict[str, int]) -> None:
    """B174, B195, B223: zero-modifier alternatives are real defaults, not missing values."""
    entry = next(e for e in inventory() if e.id == f"skill:{identifier}")
    assert entry.definition and entry.definition.skill
    assert {d.target: d.modifier for d in entry.definition.skill.defaults} == expected
    assert not entry.available


def test_mathematics_specialties_and_prerequisites() -> None:
    """B179/B182/B207/B219: explicit prerequisite identities and all six cross-defaults."""
    entries = {e.id: e for e in inventory()}
    names = {"applied", "computer-science", "cryptology", "pure", "statistics", "surveying"}
    for name in names:
        entry = entries[f"skill:mathematics-{name}"]
        assert entry.definition and entry.definition.skill
        spec = entry.definition.skill
        assert spec.specialty and spec.specialty.family == "mathematics"
        assert spec.specialty.optional_parent is None
        assert spec.reference == "B207"
        defaults = {d.target: d.modifier for d in spec.defaults}
        assert {
            key: value for key, value in defaults.items() if key.startswith("skill:mathematics-")
        } == {f"skill:mathematics-{other}": -5 for other in names - {name}}
    for name, target in {
        "astronomy": "mathematics-applied",
        "brainwashing": "psychology",
        "scuba": "swimming",
    }.items():
        definition = entries[f"skill:{name}"].definition
        assert definition and definition.skill
        assert [(p.target, p.minimum) for p in definition.skill.prerequisites] == [
            (f"skill:{target}", 1)
        ]
    # The OR prerequisite for Surgery cannot be flattened to an AND list.
    assert "prerequisite-procedure" in entries["skill:surgery"].blockers


@pytest.mark.parametrize(
    "changes",
    [
        {"page": "174"},
        {"unexpected": True},
        {"difficulty": None},
        {"blockers": []},
        {"blockers": ["typo"]},
        {"issues": [0]},
        {"issues": [112, 112]},
        {"tl_required": True},
        {"specialty_required": True},
        {
            "attribute_defaults": [
                {"attribute": "IQ", "modifier": -6},
                {"attribute": "IQ", "modifier": -5},
            ]
        },
    ],
)
def test_source_records_reject_ambiguous_metadata(changes: dict[str, object]) -> None:
    row = next(r for r in source_inventory() if r.id == "accounting").model_dump(mode="json")
    with pytest.raises(SchemaError):
        InventoryRow.model_validate_json(json.dumps(row | changes))


def test_candidate_audit_and_runtime_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    import wayfarer.rules.mundane_skills as module

    entries = inventory()
    package = candidate_package()
    assert len(package.definitions) == len(entries)
    assert all(
        d.status is ImplementationStatus.UNSUPPORTED and not d.hooks for d in package.definitions
    )
    assert all(
        e.definition is None
        or (e.definition.status is ImplementationStatus.UNSUPPORTED and not e.definition.hooks)
        for e in entries
    )
    entry = next(e for e in entries if e.definition)
    monkeypatch.setattr(module, "inventory", lambda: (replace(entry, blockers=()),))
    # Even a mistakenly cleared blocker list cannot activate an unsupported definition.
    with pytest.raises(ValidationError, match="unavailable"):
        require_available(entry.id)


def test_default_reference_validation() -> None:
    from wayfarer.rules.skill_types import SkillDefault

    entries = inventory()
    entry = entries[0]
    assert entry.definition and entry.definition.skill
    for target in ("skill:missing", entry.id):
        definition = replace(
            entry.definition,
            skill=replace(entry.definition.skill, defaults=(SkillDefault(target, -4),)),
        )
        with pytest.raises(ValidationError, match="default references"):
            validate_inventory((replace(entry, definition=definition), *entries[1:]))


def test_structural_classes_are_recorded_and_completely_sampled() -> None:
    """Classification reads recorded structure only; absent metadata stays listing-only."""
    entries = {e.id: e for e in inventory()}
    expected = {
        # B174 Accounting: an attribute default and three skill defaults.
        "skill:accounting": {"attribute-default", "skill-default"},
        # B203 Karate: no recorded default of any kind.
        "skill:karate": {"no-default"},
        # B169/B213 optional Physics specialty and its unspecialized parent.
        "skill:physics-acoustics": {"no-default", "optional-specialty"},
        # B207 Mathematics is a required specialty with TL context.
        "skill:mathematics-pure": {
            "attribute-default",
            "required-specialty",
            "skill-default",
            "technology-level",
        },
        # B230 Arm Lock is a technique of Judo, not a skill with defaults.
        "skill:arm-lock-judo": {"no-default", "technique"},
        # B176/B219 Astronomy needs a trained prerequisite and a TL.
        "skill:astronomy": {"attribute-default", "prerequisite", "technology-level"},
        # B176 Area Knowledge requires a specialty this inventory does not expand.
        "skill:area-knowledge": {"attribute-default", "unexpanded-specialty"},
        # B192/B228 Vacc Suit is a concrete Environment Suit specialty (#346).
        "skill:vacc-suit": {"attribute-default", "required-specialty", "technology-level"},
        # B233 Work by Touch is a technique of Traps, not a skill with defaults.
        "skill:work-by-touch": {"no-default", "technique"},
        # B211 Weather Sense still carries no verified mechanics at all.
        "skill:weather-sense": {"listing-only"},
    }
    for identifier, classes in expected.items():
        assert {c.value for c in entries[identifier].structural_classes} == classes
    sampled = {c for e in entries.values() for c in e.structural_classes}
    assert sampled == set(StructuralClass)
    assert all(e.structural_classes for e in entries.values())
    assert entries["skill:weather-sense"].implementation == "listing-only"
    assert entries["skill:accounting"].implementation == "unsupported"
    assert entries["skill:vacc-suit"].implementation == "implemented"
    assert sum(e.implementation == "listing-only" for e in entries.values()) == 11


def test_unsampled_or_unclassified_rows_are_rejected() -> None:
    entries = inventory()
    techniques = tuple(e for e in entries if StructuralClass.TECHNIQUE in e.structural_classes)
    assert techniques
    remaining = tuple(e for e in entries if e not in techniques)
    with pytest.raises(ValidationError, match="unsampled: technique"):
        validate_inventory(remaining)
    orphan = replace(entries[0], followup_issues=(999,))
    with pytest.raises(ValidationError, match="unowned"):
        validate_inventory((orphan, *entries[1:]))


def test_item_level_owners_stay_visible_in_the_coverage_report() -> None:
    """B208/B195: named mechanics owners survive; unowned rows are not hidden."""
    entries = {e.id: e for e in inventory()}
    assert entries["skill:broadsword"].owners == (103,)
    assert entries["skill:first-aid"].owners == (109,)
    assert entries["skill:accounting"].owners == ()
    assert coverage_blockers(PROFILE) == (103, 109, 110, 111, 112, 338, 346, 353, 356, 358)
    with pytest.raises(ValidationError, match="outside the selected profile"):
        coverage_blockers("gurps-lite-4e-2004")
    report = audit_report()
    assert report["coverage_blockers"] == [103, 109, 110, 111, 112, 338, 346, 353, 356, 358]
    assert report["runtime_owner_unassigned"] == 170
    assert report["implementation_counts"] == {
        "implemented": 83,
        "listing-only": 11,
        "unsupported": 206,
    }
    counts = report["structural_class_counts"]
    assert isinstance(counts, dict) and counts["listing-only"] == 11


def test_excluded_skills_remain_owned_by_the_catalog_that_carries_them() -> None:
    """Exclusion is a transfer with named owners, never a silent removal."""
    rows = {e.id: e for e in transferred_exclusions()}
    assert len(rows) == 28
    assert rows["alchemy"].owners == (243, 191)
    assert rows["zen-archery"].owners == (242, 191)
    assert all(row.reason and row.page for row in rows.values())


def test_exclusion_owner_drift_is_a_coverage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import wayfarer.rules.mundane_skills as module

    rows = exclusions()
    monkeypatch.setattr(module, "exclusions", lambda: rows[1:])
    with pytest.raises(ValidationError, match="owning catalog differ"):
        transferred_exclusions()
    renamed = rows[0].model_copy(update={"owners": (191,)})
    monkeypatch.setattr(module, "exclusions", lambda: (renamed, *rows[1:]))
    with pytest.raises(ValidationError, match="owner drift"):
        transferred_exclusions()


@pytest.mark.parametrize(
    "changes", [{"owners": []}, {"owners": [242, 242]}, {"owners": [0]}, {"id": "Alchemy"}]
)
def test_exclusion_records_reject_unowned_transfers(changes: dict[str, object]) -> None:
    row = exclusions()[0].model_dump(mode="json")
    with pytest.raises(SchemaError):
        Exclusion.model_validate_json(json.dumps(row | changes))
