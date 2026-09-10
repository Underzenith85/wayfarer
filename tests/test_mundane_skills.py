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
    source_index,
    source_inventory,
    transferred_exclusions,
    validate_inventory,
    validate_source_index,
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
        {"procedure_owner": 112},
        {"procedure_owner": 336},
        {"issues": [112, 341]},
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
    # The accounting package never carries a hook, even for a row whose runtime
    # procedure is bound in a separate campaign pin.
    assert all(
        e.definition is None
        or (e.definition.status is ImplementationStatus.UNSUPPORTED and not e.definition.hooks)
        for e in entries
        if not e.bound
    )
    bow = next(e for e in entries if e.id == "skill:bow")
    assert bow.bound and bow.dispatch == "combat.ranged-attack"
    assert bow.definition is not None
    assert bow.definition.status is ImplementationStatus.IMPLEMENTED
    # #191's printing delta still blocks every row, so nothing is runtime-available.
    assert all(e.blockers for e in entries) and not bow.available
    with pytest.raises(ValidationError, match="unavailable"):
        require_available("skill:bow")
    entry = next(e for e in entries if e.definition and not e.bound)
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
        "skill:physics-acoustics": {"no-default", "optional-specialty", "technology-level"},
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
        # B192: distinct suit skill with all numeric defaults.
        "skill:vacc-suit": {
            "attribute-default",
            "skill-default",
            "required-specialty",
            "technology-level",
        },
        "skill:weather-sense": {"attribute-default", "technology-level", "alias"},
        "skill:neck-snap": {"listing-only", "technique-template"},
    }
    for identifier, classes in expected.items():
        assert {c.value for c in entries[identifier].structural_classes} == classes
    sampled = {c for e in entries.values() for c in e.structural_classes}
    assert sampled == set(StructuralClass)
    assert all(e.structural_classes for e in entries.values())
    assert entries["skill:neck-snap"].implementation == "listing-only"
    assert entries["skill:accounting"].implementation == "unsupported"
    assert sum(e.implementation == "listing-only" for e in entries.values()) == 28


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
    assert entries["skill:broadsword"].owners == (339,)
    assert entries["skill:first-aid"].owners == (342,)
    assert entries["skill:accounting"].owners == (341,)
    # #344 keeps the ranged rows it did not implement visible under the concrete
    # children that own them, instead of resolving them into its own number.
    assert entries["skill:bow"].owners == (344,)
    assert entries["skill:bolas"].owners == (344,)
    assert entries["skill:net"].blocker_owners == {
        "first-printing-delta-audit": (336,),
        "conditional-or-skill-defaults": (336, 362),
    }
    assert entries["skill:guns"].owners == (344,)
    assert entries["skill:artillery"].owners == (344, 357)
    assert coverage_blockers(PROFILE) == (
        103,
        109,
        110,
        111,
        112,
        336,
        338,
        339,
        340,
        341,
        342,
        343,
        344,
        345,
        346,
        353,
        356,
        357,
        358,
        359,
        360,
        361,
        362,
        366,
        367,
        368,
        369,
        370,
    )
    with pytest.raises(ValidationError, match="outside the selected profile"):
        coverage_blockers("gurps-lite-4e-2004")
    report = audit_report()
    assert report["coverage_blockers"] == [
        103,
        109,
        110,
        111,
        112,
        336,
        338,
        339,
        340,
        341,
        342,
        343,
        344,
        345,
        346,
        353,
        356,
        357,
        358,
        359,
        360,
        361,
        362,
        366,
        367,
        368,
        369,
        370,
    ]
    assert report["runtime_owner_unassigned"] == 0
    assert report["implementation_counts"] == {
        # 27 ranged (#344, #354, #355), 16 social (#345) and 83 technology
        # (#346) rows dispatch a real procedure.
        "implemented": 126,
        "listing-only": 28,
        "unsupported": 189,
    }
    # A bound row can still leave part of its entry to another issue; that gap is
    # published rather than folded into the blocker list.
    scope = report["transferred_procedure_scope"]
    assert isinstance(scope, list)
    assert {str(row["skill"]) for row in scope} == {
        "skill:carousing",
        "skill:interrogation",
        "skill:leadership",
        "skill:panhandling",
        "skill:performance",
        "skill:public-speaking",
        "skill:teaching",
    }
    assert all(row["owner_issue"] in (368, 369, 370) and row["detail"] for row in scope)
    counts = report["structural_class_counts"]
    assert isinstance(counts, dict) and counts["listing-only"] == 28


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


def indexed_expansions(index: object, parent: str) -> int:
    return sum(e.parent == parent for e in index.entries)  # type: ignore[attr-defined]


def test_independent_source_index_accounts_for_every_listing() -> None:
    """Characters third printing B301-304: 275 skills and 27 named techniques."""
    index = source_index()
    assert index.baseline_reconciled is False
    assert "third printing" in index.observed_source
    assert len([e for e in index.entries if e.kind == "skill"]) == 275
    assert len([e for e in index.entries if e.kind == "technique"]) == 27
    # #344 expands Thrown Weapon and #355 the two TL-indexed weapon families.
    assert len([e for e in index.entries if e.kind == "expansion"]) == 68
    assert indexed_expansions(index, "thrown-weapon") == 7
    indexed = {e.id: e for e in index.entries}
    assert indexed["brain-hacking"].page == 182
    assert indexed["melee-weapon"].page == 208
    assert indexed["combat-art-or-sport"].targets == ("combat-art", "combat-sport")
    assert indexed["kicking"].page == 231
    assert indexed["kicking-karate"].parent == "kicking"
    assert indexed["neck-snap"].kind == "technique"
    assert indexed["work-by-touch"].page == 233
    validate_source_index(inventory(), exclusions())


@pytest.mark.parametrize("identifier", ["accounting", "brain-hacking", "neck-snap"])
def test_index_detects_deletion_even_when_other_structural_classes_survive(identifier: str) -> None:
    entries = tuple(e for e in inventory() if e.id != f"skill:{identifier}")
    with pytest.raises(ValidationError, match="Source index accounting mismatch"):
        validate_source_index(entries, exclusions())


def test_index_rejects_unindexed_rows_and_page_drift() -> None:
    entries = inventory()
    with pytest.raises(ValidationError, match="unindexed"):
        validate_source_index((*entries, replace(entries[0], id="skill:invented")), exclusions())
    with pytest.raises(ValidationError, match="page mismatch"):
        validate_source_index((replace(entries[0], reference="B200"), *entries[1:]), exclusions())
    with pytest.raises(ValidationError, match="missing"):
        validate_source_index(entries, exclusions()[1:])


@pytest.mark.parametrize(
    ("identifier", "page", "attribute", "difficulty", "defaults"),
    [
        ("aerobatics", 174, "attribute:dx", "hard", {"attribute:dx": -6}),
        ("aquabatics", 174, "attribute:dx", "hard", {"attribute:dx": -6}),
        ("airshipman", 185, "attribute:iq", "easy", {"attribute:iq": -4}),
        ("seamanship", 185, "attribute:iq", "easy", {"attribute:iq": -4}),
        ("spacer", 185, "attribute:iq", "easy", {"attribute:iq": -4}),
        ("submariner", 185, "attribute:iq", "easy", {"attribute:iq": -4}),
        (
            "battlesuit",
            192,
            "attribute:dx",
            "average",
            {
                "attribute:dx": -5,
                "skill:diving-suit": -4,
                "skill:nbc-suit": -2,
                "skill:vacc-suit": -2,
            },
        ),
        (
            "diving-suit",
            192,
            "attribute:dx",
            "average",
            {
                "attribute:dx": -5,
                "skill:battlesuit": -4,
                "skill:nbc-suit": -4,
                "skill:vacc-suit": -4,
                "skill:scuba": -2,
            },
        ),
        (
            "nbc-suit",
            192,
            "attribute:dx",
            "average",
            {
                "attribute:dx": -5,
                "skill:battlesuit": -2,
                "skill:diving-suit": -4,
                "skill:vacc-suit": -2,
            },
        ),
        (
            "vacc-suit",
            192,
            "attribute:dx",
            "average",
            {
                "attribute:dx": -5,
                "skill:battlesuit": -2,
                "skill:diving-suit": -4,
                "skill:nbc-suit": -2,
            },
        ),
        (
            "jitte-sai",
            208,
            "attribute:dx",
            "average",
            {
                "skill:force-sword": -4,
                "skill:main-gauche": -4,
                "skill:shortsword": -3,
            },
        ),
        (
            "force-whip",
            209,
            "attribute:dx",
            "average",
            {
                "skill:kusari": -3,
                "skill:monowire-whip": -3,
                "skill:whip": -3,
            },
        ),
        (
            "kusari",
            209,
            "attribute:dx",
            "hard",
            {
                "skill:force-whip": -3,
                "skill:monowire-whip": -3,
                "skill:whip": -3,
                "skill:two-handed-flail": -4,
            },
        ),
        (
            "broadsword",
            208,
            "attribute:dx",
            "average",
            {
                "skill:force-sword": -4,
                "skill:rapier": -4,
                "skill:saber": -4,
                "skill:shortsword": -2,
                "skill:two-handed-sword": -4,
            },
        ),
    ],
)
def test_previously_unstructured_source_metadata(
    identifier: str, page: int, attribute: str, difficulty: str, defaults: dict[str, int]
) -> None:
    """Independent B174/B185/B192/B208-209 numeric expectations; no synthesized defaults."""
    entry = next(e for e in inventory() if e.id == f"skill:{identifier}")
    assert entry.reference == f"B{page}"
    assert entry.definition and entry.definition.skill
    spec = entry.definition.skill
    assert spec.attribute == attribute
    assert spec.difficulty == difficulty
    assert {d.target: d.modifier for d in spec.defaults} == defaults
    assert not entry.available


def test_alias_and_technique_context_stays_explicit() -> None:
    """B209 Weather Sense is low-TL Meteorology; B230-232 techniques aren't skills."""
    entries = {e.id: e for e in inventory()}
    assert entries["skill:weather-sense"].alias_of == "skill:meteorology"
    for id, parent, modifier, maximum, page in [
        ("arm-lock-judo", "judo", 0, 4, 230),
        ("kicking-karate", "karate", -2, 0, 231),
    ]:
        entry = entries[f"skill:{id}"]
        assert entry.reference == f"B{page}"
        assert entry.definition and entry.definition.skill and entry.definition.skill.technique
        technique = entry.definition.skill.technique
        assert (technique.parent, technique.default_modifier, technique.maximum_modifier) == (
            f"skill:{parent}",
            modifier,
            maximum,
        )
    # ST-based Neck Snap and cinematic choices must not become generic DX rolls.
    assert entries["skill:neck-snap"].definition is None
    for id in ("dual-weapon-attack", "whirlwind-attack"):
        assert "optional-rule-selection" in entries[f"skill:{id}"].blockers
    assert "prerequisite-procedure" in entries["skill:brain-hacking"].blockers


def test_every_blocker_has_a_named_followup() -> None:
    for entry in inventory():
        assert set(entry.blocker_owners) == set(entry.blockers)
        assert entry.procedure_owner not in (112, 191, 336)
        assert entry.blocker_owners["first-printing-delta-audit"] == (336,)
        assert all(
            set(owners) <= set(entry.followup_issues) for owners in entry.blocker_owners.values()
        )
        # A procedure owner that split a blocker into a bounded child keeps that
        # child visible; every other row still resolves to its single owner.
        assert entry.owners[0] == entry.procedure_owner
        assert set(entry.owners) <= set(entry.followup_issues)
        assert entry.owners == (entry.procedure_owner,) or entry.transferred


def test_alias_and_owner_validation() -> None:
    entries = inventory()
    first = entries[0]
    for target in (first.id, "skill:missing"):
        with pytest.raises(ValidationError, match="alias reference"):
            validate_inventory((replace(first, alias_of=target), *entries[1:]))
    with pytest.raises(ValidationError, match="Cyclic alias"):
        validate_inventory(
            (
                replace(first, alias_of=entries[1].id),
                replace(entries[1], alias_of=first.id),
                *entries[2:],
            )
        )
    with pytest.raises(ValidationError, match="Unowned blocker"):
        validate_inventory(
            (replace(first, followup_issues=(112, first.procedure_owner)), *entries[1:])
        )


def test_candidate_inventory_does_not_inherit_live_representative_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wayfarer.rules.gurps_skills as live

    before = candidate_package().digest
    monkeypatch.setattr(live, "definitions", lambda _: ())
    assert candidate_package().digest == before


def test_invalid_acquisition_cycles_are_rejected_but_mutual_defaults_survive() -> None:
    from wayfarer.rules.skill_types import SkillPrerequisite, Technique

    entries = inventory()
    validate_inventory(entries)  # Source contains reciprocal weapon defaults.
    first, second = entries[:2]
    assert first.definition and first.definition.skill
    assert second.definition and second.definition.skill
    cyclic_first = replace(
        first,
        definition=replace(
            first.definition,
            skill=replace(
                first.definition.skill,
                prerequisites=(SkillPrerequisite(second.id),),
            ),
        ),
    )
    cyclic_second = replace(
        second,
        definition=replace(
            second.definition,
            skill=replace(
                second.definition.skill,
                prerequisites=(SkillPrerequisite(first.id),),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="Cyclic prerequisite"):
        validate_inventory((cyclic_first, cyclic_second, *entries[2:]))
    self_parent = replace(
        first,
        definition=replace(
            first.definition,
            skill=replace(
                first.definition.skill,
                technique=Technique(first.id, -2),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="Cyclic prerequisite or technique"):
        validate_inventory((self_parent, *entries[1:]))
