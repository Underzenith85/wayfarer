"""Source-derived Roadside Watch build and equipment admission expectations."""

from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError as ModelValidationError

from wayfarer.adventures import roadwatch_rules as rules
from wayfarer.certification.source_ledgers import BASELINE_ID, SOURCE_DIGESTS
from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal
from wayfarer.engine.character.statistics import encumbrance
from wayfarer.engine.rules.gurps_characters import Encumbrance
from wayfarer.engine.rules.profiles import GURPS_PROPAGANDA_PROFILE
from wayfarer.engine.rules.skills.mundane import medicine, melee
from wayfarer.engine.rules.types.battle_fatigue import BattleFatiguePolicy
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.errors import ValidationError


@pytest.mark.parametrize(
    ("actor_id", "attributes", "expected_skills", "hp", "fp", "speed", "lift"),
    [
        (
            "a",
            {"st": 11, "dx": 12, "iq": 11, "ht": 11},
            {"broadsword": 14, "stealth": 13, "observation": 13},
            11,
            11,
            Decimal("5.75"),
            Decimal(24),
        ),
        (
            "b",
            {"st": 10, "dx": 11, "iq": 12, "ht": 11},
            {"first-aid": 13, "broadsword": 12, "observation": 15, "stealth": 14},
            10,
            11,
            Decimal("5.5"),
            Decimal(20),
        ),
    ],
)
def test_fixed_pcs_compile_actual_paid_skills_and_secondary_statistics(
    actor_id: str,
    attributes: dict[str, int],
    expected_skills: dict[str, int],
    hp: int,
    fp: int,
    speed: Decimal,
    lift: Decimal,
) -> None:
    result = rules.compiler().compile(rules.proposal(actor_id).draft)
    assert result.legal and result.spent == 100 and result.remaining == 0
    build = result.build
    assert build is not None and build.statistics is not None
    values = {value.target: value.value for value in build.sheet.values}
    assert {key: values["attribute:" + key] for key in attributes} == attributes
    assert {key: values["skill:" + key] for key in expected_skills} == expected_skills
    assert (build.statistics.hp, build.statistics.fp) == (hp, fp)
    assert (build.statistics.basic_speed, build.statistics.basic_lift) == (speed, lift)
    assert (build.statistics.basic_move, build.statistics.dodge) == (5, 8)
    assert encumbrance(rules.PROFILE_ID, lift, Decimal(15)) is Encumbrance.NONE
    assert all(entry.cost >= 0 for entry in build.purchases)
    if actor_id == "b":
        first_aid = next(p for p in build.purchases if p.definition_id == "skill:first-aid")
        assert first_aid.technology_level == 3 and first_aid.cost == 2


def test_weak_gm_npc_is_an_actual_compiled_human_build() -> None:
    result = rules.compiler().compile(rules.proposal("brigand").draft)
    assert result.legal and result.spent == 2 and result.remaining == 98
    assert result.build is not None and result.build.statistics is not None
    values = {value.target: value.value for value in result.build.sheet.values}
    assert values["skill:broadsword"] == 10
    assert (result.build.statistics.hp, result.build.statistics.fp) == (10, 10)


def test_new_pin_is_explicit_and_does_not_change_basic_v11() -> None:
    assert rules.SOURCE_BASELINE == BASELINE_ID
    assert set(rules.SOURCE_SHA256.values()) == set(SOURCE_DIGESTS.values())
    assert all(
        rules.SOURCE_SHA256[source.id] in (source.citation or "")
        for source in rules.PACKAGE.sources
    )
    assert (
        rules.PACKAGE.digest == "6b9e161a3aa81ecfe4294ae9e06c474c5fa29bbd009b4b816899ffdbca421feb"
    )
    assert rules.PACKAGE.version == "1.1.0"
    assert rules.PACKAGE.battle_fatigue == BattleFatiguePolicy(maximum_seconds=119)
    assert rules.compiler().battle_fatigue == rules.PACKAGE.battle_fatigue
    old = replace(rules.PACKAGE, version="1.0.0", battle_fatigue=None)
    assert old.digest == "515f50869f1e7794ae48ebf63cfd61e5a5dca51cd48e8412cd4f00cc7f580d00"
    assert '"battle_fatigue"' not in old.canonical_json()
    assert rules.RULES_REFERENCE["policy_id"] == "policy:roadside-watch-gurps"
    assert rules.POLICY.technology_level == 3 and not rules.POLICY.allow_supernatural
    assert GURPS_PROPAGANDA_PROFILE.policy.technology_level == 8
    assert GURPS_PROPAGANDA_PROFILE.packages[0].version == "0.9.0"
    assert (
        GURPS_PROPAGANDA_PROFILE.digest
        == "483b2f942994f1ca2f0e23e8825e1cfd172fc9bddac5e9bf1aab3ec86f120a0d"
    )


def test_canonical_skill_metadata_and_external_defaults_are_not_erased() -> None:
    installed = {definition.id: definition for definition in rules.PACKAGE.definitions}
    canonical_first_aid = next(d for d in medicine.definitions() if d.id == "skill:first-aid")
    canonical_broadsword = next(
        d for d in melee.definitions(default_revision=2) if d.id == "skill:broadsword"
    )
    assert installed["skill:first-aid"] == canonical_first_aid
    assert installed["skill:broadsword"] == canonical_broadsword
    assert canonical_first_aid.skill is not None
    assert any(
        default.target == "skill:physician" for default in canonical_first_aid.skill.defaults
    )
    assert "skill:physician" not in installed
    assert {
        definition.id for definition in rules.PACKAGE.definitions if definition.skill is not None
    } == rules.SKILL_IDS


@pytest.mark.parametrize(
    "definition_id",
    ["skill:physician", "skill:diagnosis", "skill:shield-shield", "spell:light", "trait:magery"],
)
def test_outside_skill_trait_and_magic_purchases_cannot_activate(definition_id: str) -> None:
    base = rules.proposal("b").draft
    result = rules.compiler().compile(
        base.model_copy(
            update={"purchases": base.purchases + (Purchase(definition_id=definition_id),)}
        )
    )
    assert not result.legal and result.build is None
    assert "definition.unknown" in {diagnostic.code for diagnostic in result.diagnostics}


def test_tl_admission_point_budget_and_fixed_pregen_gate_are_distinct() -> None:
    base = rules.proposal("b")
    missing_tl = tuple(
        p.model_copy(update={"technology_level": None})
        if p.definition_id == "skill:first-aid"
        else p
        for p in base.draft.purchases
    )
    result = rules.compiler().compile(base.draft.model_copy(update={"purchases": missing_tl}))
    assert not result.legal and "skill.technology_level" in {d.code for d in result.diagnostics}
    wrong_tl = tuple(
        p.model_copy(update={"technology_level": 4}) if p.definition_id == "skill:first-aid" else p
        for p in base.draft.purchases
    )
    # The general compiler can represent off-campaign TL knowledge. This limited
    # campaign admits the exact TL3 pregen, not every generally legal build.
    with pytest.raises(ValidationError, match="fixed character proposals"):
        rules.require_pregen(
            "b", CharacterProposal(draft=base.draft.model_copy(update={"purchases": wrong_tl}))
        )
    overspent = CharacterDraft(
        name="Over budget",
        purchases=tuple(
            Purchase(definition_id="attribute:" + name, amount=12)
            for name in ("st", "dx", "iq", "ht")
        ),
    )
    result = rules.compiler().compile(overspent)
    assert not result.legal and "budget.overspent" in {d.code for d in result.diagnostics}
    rules.require_pregen("b", base)
    alternative = CharacterProposal(
        draft=base.draft.model_copy(update={"name": "A different fixed proposal"})
    )
    assert rules.compiler().compile(alternative.draft).legal
    with pytest.raises(ValidationError, match="fixed character proposals"):
        rules.require_pregen("b", alternative)
    with pytest.raises(ValidationError, match="Unknown"):
        rules.proposal("other")
    with pytest.raises(ModelValidationError):
        Purchase.model_validate({"definition_id": "skill:first-aid", "amount": 2, "level": 99})


def test_selected_equipment_is_exact_canonical_gear_with_honest_load_and_coverage() -> None:
    canonical = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}
    admitted = {entry.definition_id: entry for entry in rules.EQUIPMENT.entries}
    assert set(admitted) == rules.EQUIPMENT_IDS
    assert all(
        entry == canonical[identifier]
        for identifier, entry in admitted.items()
        if identifier != "equipment:broadsword"
    )
    sword, armor, bandages = (
        admitted["equipment:" + key] for key in ("broadsword", "leather-armor", "bandages")
    )
    canonical_sword = canonical["equipment:broadsword"]
    # Local authoring supplies only the B274 quality needed by B556. This must
    # not quietly replace damage, durability, prices, modes or global metadata.
    assert sword.model_dump(exclude={"critical_breakage", "provenance"}) == (
        canonical_sword.model_dump(exclude={"critical_breakage", "provenance"})
    )
    assert sword.critical_breakage == "ordinary"
    assert sword.provenance == canonical_sword.provenance.model_copy(update={"pages": (271, 274)})
    assert canonical_sword.critical_breakage is None
    assert canonical_sword.provenance.pages == (271,)
    assert (sword.price, sword.weight_millipounds, sword.technology_level) == (500, 3000, 2)
    assert {(mode.id, mode.skill_id) for mode in sword.modes} == {
        ("swing", "skill:broadsword"),
        ("thrust", "skill:broadsword"),
    }
    assert (armor.price, armor.weight_millipounds) == (100, 10000)
    assert armor.armor is not None and armor.armor.dr == 2
    assert armor.armor.locations == ("torso", "groin")
    assert (bandages.price, bandages.weight_millipounds) == (10, 2000)
    assert len(bandages.general) == 1
    feature = bandages.general[0]
    assert (feature.kind, feature.skill_id, feature.modifier, feature.capacity) == (
        "medical",
        "skill:first-aid",
        0,
        6,
    )
    assert feature.technology_relative
    assert rules.EQUIPMENT_SPECS == rules.EQUIPMENT.bind((rules.PACKAGE,))
    assert {spec.definition_id for spec in rules.EQUIPMENT_SPECS} == rules.POLICY.allowed_equipment
    assert "equipment:first-aid-kit" not in rules.POLICY.allowed_equipment


def test_policy_pin_mismatch_fails_closed() -> None:
    with pytest.raises(ValidationError, match="policy pin"):
        CharacterCompiler(
            rules.CATALOG,
            rules.RULES,
            replace(rules.POLICY, version=2),
            statistics_profile=rules.PROFILE_ID,
        )
