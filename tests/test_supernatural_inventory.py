"""Independent index expectations: Characters third printing B297-306/B255-257.

Numeric runtime examples reference B46/B48/B61/B69/B106/B111/B235/B249-250.
These compare observed-printing evidence, not the unreconciled frozen baseline.
"""

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError as ModelValidationError

from wayfarer.engine.rules import conformance
from wayfarer.engine.rules.abilities import validate_binding
from wayfarer.engine.rules.ability_types import AbilitySpec
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    ImplementationStatus,
    PackagePin,
    RulesCatalog,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.rules.conformance import CoverageStatus
from wayfarer.engine.rules.supernatural import (
    PROFILE,
    Entry,
    Inventory,
    coverage_blockers,
    definition,
    inventory,
    lookup,
    require_entries,
    require_family,
)
from wayfarer.engine.rules.traits import TraitOptions
from wayfarer.errors import ValidationError

# Independently transcribed source index names, not generated from package data.
SPELLS = """
Accuracy|Analyze Magic|Apportation|Armor|Aura|Awaken|Banish|Blur|Breathe Water|
Clumsiness|Cold|Command|Continual Light|Counterspell|Create Air|Create Earth|
Create Fire|Create Water|Darkness|Daze|Death Vision|Deathtouch|Deflect Energy|
Deflect Missile|Deflect|Destroy Water|Detect Magic|Dispel Magic|Earth to Air|
Earth to Stone|Enchant|Entombment|Explosive Fireball|Extinguish Fire|Fireball|
Flesh to Stone|Fog|Foolishness|Forgetfulness|Fortify|Great Haste|Great Healing|
Haste|Heat|Hide Thoughts|Hinder|Icy Weapon|Identify Spell|Ignite Fire|Itch|
Lend Energy|Lend Vitality|Light|Lightning|Lockmaster|Magelock|Major Healing|
Mass Daze|Mass Sleep|Mind-Reading|Minor Healing|No-Smell|Pain|Paralyze Limb|
Planar Summons|Plane Shift|Power|Predict Weather|Puissance|Purify Air|Purify Water|
Recover Energy|Resist Cold|Resist Fire|Rooted Feet|Seek Earth|Seek Water|Seeker|
Sense Emotion|Sense Foes|Sense Spirit|Shape Air|Shape Earth|Shape Fire|Shape Water|
Shield|Sleep|Spasm|Staff|Stench|Stone to Earth|Stone to Flesh|Summon Demon|
Summon Spirit|Trace|Truthsayer|Turn Zombie|Walk on Air|Wither Limb|Zombie
"""

SUPERNATURAL = """
Blessed|Channeling|Clairsentience|Destiny|Dominance|Higher Purpose|Illuminated|
Jumper|Magery|Magic Resistance|Mana Damper|Mana Enhancer|Medium|Mindlink|Oracle|
Power Investiture|Precognition|Psi Static|Psychometry|Reawakened|Snatcher|
Special Rapport|Spirit Empathy|Super Luck|Supernatural Durability|Temporal Inertia|
Terror|True Faith|Visualization|Warp|Wild Talent
"""


def names(text: str) -> set[str]:
    return {name.strip() for name in text.split("|")}


def test_complete_source_index_and_distinct_spell_trait_names() -> None:
    rows = inventory().entries
    assert Counter(e.kind for e in rows) == {
        "spell": 100,
        "advantage": 150,
        "disadvantage": 42,
        "power": 6,
        "protocol": 8,
        "skill": 28,
    }
    assert {e.name for e in rows if e.kind == "spell"} == names(SPELLS)
    assert {e.name for e in rows if e.kind == "advantage" and e.classification == "Sup"} == names(
        SUPERNATURAL
    )
    assert lookup("spell:mind-reading").page == 245
    assert lookup("advantage:mind-reading").page == 69
    assert lookup("advantage:destiny").page == 48
    assert lookup("disadvantage:destiny").page == 131
    assert lookup("advantage:360-vision").page == 34


def test_spells_include_campaigns_enchantments_and_cross_college_membership() -> None:
    rows = [e for e in inventory().entries if e.kind == "spell"]
    assert {e.name for e in rows if e.source == "campaigns-fourth"} == names(
        "Accuracy|Deflect|Enchant|Fortify|Power|Puissance|Staff"
    )
    assert sum(e.source == "characters-third" for e in rows) == 93
    assert {e.name for e in rows if e.difficulty == "VH"} == names(
        "Enchant|Great Haste|Great Healing|Major Healing|Plane Shift"
    )
    assert lookup("spell:breathe-water").colleges == ("air", "water")
    assert lookup("spell:earth-to-air").colleges == ("air", "earth")
    assert lookup("spell:hinder").colleges == ("body-control", "movement")
    assert lookup("spell:staff").page == 481


def test_psi_inventory_includes_conditional_members_and_no_antipsi_talent() -> None:
    powers = [e for e in inventory().entries if e.kind == "power"]
    assert {e.name for e in powers} == names(
        "Antipsi|ESP|Psychic Healing|Psychokinesis|Telepathy|Teleportation"
    )
    for power in powers:
        assert (power.talent_cost, power.power_modifier) == (
            (None, 0) if power.name == "Antipsi" else (5, -10)
        )
    assert len(lookup("power:esp").members) == 12
    assert len(lookup("power:telepathy").members) == 15
    assert "advantage:affliction" in lookup("power:telepathy").members
    assert any("Malediction" in c for c in lookup("power:telepathy").member_conditions)
    assert any("Force Field" in c for c in lookup("power:psychokinesis").member_conditions)


def test_every_entry_has_concrete_runtime_and_source_blockers_and_real_evidence() -> None:
    data = inventory()
    assert {s.printing for s in data.sources} == {3, 4}
    assert all(not s.baseline_reconciled and s.errata_overlay is None for s in data.sources)
    for entry in data.entries:
        assert entry.status is not CoverageStatus.VERIFIED
        assert 191 in entry.blockers
        if not any(221 <= n <= 243 for n in entry.blockers):
            assert entry.status is CoverageStatus.PARTIAL
            assert entry.supported_subset and entry.evidence
        assert all(Path(path).is_file() for path in entry.evidence)
    assert set(coverage_blockers(PROFILE)) == {
        107,
        173,
        191,
        *(
            n
            for n in range(221, 244)
            if n
            not in {
                221,
                222,
                223,
                224,
                225,
                226,
                227,
                228,
                229,
                230,
                231,
                232,
                233,
                234,
                235,
                236,
                237,
                238,
                239,
                240,
                241,
                242,
                243,
            }
        ),
    }
    assert {e.name for e in data.entries if e.optional} == {"Clerical Magic", "Ritual Magic"}


@pytest.mark.parametrize("identifier", ["spell:light", "spell:shape-fire", "power:telepathy"])
def test_partial_and_learning_only_entries_are_not_execution_permissions(identifier: str) -> None:
    with pytest.raises(ValidationError, match="not certified"):
        require_entries(PROFILE, (identifier,))


@pytest.mark.parametrize("selected", ["invented", "gurps-lite-4e-2004"])
def test_unknown_or_out_of_scope_profiles_reject_even_empty_requirements(selected: str) -> None:
    with pytest.raises(ValidationError):
        require_entries(selected, ())
    with pytest.raises(ValidationError):
        coverage_blockers(selected)


def test_unknown_names_and_nonpurchasable_protocols_reject() -> None:
    assert require_entries(PROFILE, ()) == ()
    with pytest.raises(ValidationError, match="Unknown supernatural entry"):
        lookup("spell:made-up")
    with pytest.raises(ValidationError, match="not purchasable"):
        definition("protocol:ritual-magic")
    with pytest.raises(ValidationError, match="Unknown supernatural family"):
        require_family("gurps.fake")


@pytest.mark.parametrize("family", ["gurps.magic.spellcasting", "gurps.supernatural.abilities"])
def test_promoting_family_flag_cannot_hide_item_blockers(
    family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = dict(conformance.CAPABILITIES)
    entries[family] = replace(entries[family], status=CoverageStatus.VERIFIED)
    monkeypatch.setattr(conformance, "CAPABILITIES", entries)
    with pytest.raises(ValidationError, match="not certified"):
        conformance.require_verified(family)


def test_catalog_activation_rejects_audit_record_even_with_source_permission() -> None:
    record = definition("spell:light")
    assert record.status is ImplementationStatus.UNSUPPORTED
    source = SourceReference(record.source_id, "Observed source audit", "user-supplied-reference")
    package = RulesPackage("audit-test", "1", "test", (source,), (record,))
    policy = CampaignPolicy(
        "audit", 1, 100, 50, 20, 30, frozenset({record.source_id}), allow_supernatural=True
    )
    rules = CampaignRules(
        "test", (PackagePin(package.id, package.version, package.digest),), "audit", 1
    )
    with pytest.raises(ValidationError, match="Unsupported definition cannot activate"):
        RulesCatalog((package,)).activate(rules, policy)


@pytest.mark.parametrize("change", ["blockers", "source", "member", "verified"])
def test_malformed_or_falsely_certified_inventory_rejects(change: str) -> None:
    data = inventory().model_dump(mode="json")
    # JSON input is kept untyped only at the explicit serialization boundary.
    import json

    raw = json.loads(inventory().model_dump_json())
    if change == "blockers":
        raw["entries"][0]["blockers"] = []
    elif change == "source":
        raw["entries"][0]["source"] = "invented"
    elif change == "member":
        next(e for e in raw["entries"] if e["kind"] == "power")["members"] = ["advantage:fake"]
    else:
        raw["entries"][0].update(
            status="verified",
            blockers=[],
            supported_subset="manual",
            evidence=["tests/test_supernatural_inventory.py"],
        )
    assert data["version"] == 1
    with pytest.raises(ModelValidationError):
        Inventory.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "kind,level,modifiers,expected",
    [
        ("burning-malediction", 3, ("malediction-1", "costs-fatigue-1"), 30),
        ("damage-resistance", 5, ("costs-fatigue-1",), 24),
        ("detect", 1, ("precise", "costs-fatigue-1"), 10),
        ("mind-reading", 1, ("telepathic", "costs-fatigue-2"), 24),
    ],
)
def test_source_compared_representative_modified_costs(
    kind: str, level: int, modifiers: tuple[str, ...], expected: int
) -> None:
    # B46/B48/B61/B69 base costs; B106/B111 modifiers; B101 round upward.
    spec = AbilitySpec.model_validate(
        {"definition_id": "audit-example", "kind": kind, "modifiers": modifiers}
    )
    assert validate_binding(spec, level, TraitOptions(modifiers=modifiers)) == expected


def test_partial_entry_without_subset_evidence_is_invalid() -> None:
    data = lookup("spell:light").model_dump_json()
    import json

    raw = json.loads(data)
    raw["evidence"] = []
    with pytest.raises(ModelValidationError, match="subset evidence"):
        Entry.model_validate_json(json.dumps(raw))


def test_transferred_skills_and_source_audit_use_the_complete_owner_inventory() -> None:
    from wayfarer.certification.source_audit import inventory as source_inventory
    from wayfarer.engine.rules.mundane_skills import exclusions

    assert {(e.name, e.page) for e in inventory().entries if e.kind == "skill"} == {
        (e.name, e.page) for e in exclusions()
    }
    assert lookup("skill:alchemy").blockers == (191,)
    assert lookup("skill:zen-archery").blockers == (191,)
    owned = [e for e in source_inventory() if e.id.startswith("supernatural/")]
    assert len(owned) == 334
    assert {e.id for e in owned} == {"supernatural/" + e.id for e in inventory().entries}
    assert all(e.source_review == "pending" and e.owner == 119 for e in owned)
    assert {n for e in owned for n in e.blockers} == set(coverage_blockers(PROFILE))
