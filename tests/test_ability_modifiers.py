"""Independent B101-118 modifier construction and runtime fixtures."""

from decimal import Decimal
from pathlib import Path

import pytest

from wayfarer.certification.source_ledgers import load_source_ledgers
from wayfarer.engine.rules.traits.modifiers import (
    ACCURATE,
    AREA,
    BREAKABLE,
    INACCURATE,
    INCREASED_RANGE,
    MODIFIER_INDEX,
    PROFILE,
    STOLEN,
    UNIQUE,
    AbilityDefinition,
    AlternativeAbilities,
    AttackProfile,
    GadgetConstruction,
    GadgetState,
    ModifierApproval,
    ModifierSelection,
    apply_attack_modifiers,
    gadget_available,
    modified_cost,
)
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def selection(
    identifier: str,
    *,
    level: int = 1,
    option: str | None = None,
    limited_by: ModifierSelection | None = None,
    gadget: GadgetConstruction | None = None,
) -> ModifierSelection:
    return ModifierSelection(
        definition_id=identifier,
        level=level,
        option=option,
        limited_by=limited_by,
        gadget=gadget,
    )


def test_all_87_source_rows_have_distinct_modifier_definitions() -> None:
    source = load_source_ledgers(ROOT).by_type["modifiers"]
    assert len(source) == len(MODIFIER_INDEX) == 87
    assert {row.id for row in source} == set(MODIFIER_INDEX)
    assert all(row.construction_binding == row.id for row in source)
    assert all(row.cost_owner and row.consequence_owner == 497 for row in source)


def test_positive_negative_level_cost_and_final_rounding() -> None:
    result = modified_cost(
        11,
        "innate-attack",
        (
            selection(ACCURATE, level=2),
            selection("modifier:limitation:costs-fatigue", level=1),
        ),
    )
    assert result.net_percent == 5
    assert result.final_cost == 12
    assert [component.percent for component in result.components] == [10, -5]


def test_limitations_are_capped_at_eighty_percent() -> None:
    result = modified_cost(
        50,
        "innate-attack",
        (selection("modifier:limitation:costs-fatigue", level=20),),
    )
    assert (result.net_percent, result.final_cost) == (-80, 10)


def test_invalid_subject_duplicate_and_incompatible_combinations_fail_closed() -> None:
    with pytest.raises(ValidationError, match="unavailable"):
        modified_cost(10, "advantage", (selection(ACCURATE),))
    with pytest.raises(ValidationError, match="Duplicate"):
        modified_cost(10, "innate-attack", (selection(ACCURATE), selection(ACCURATE)))
    with pytest.raises(ValidationError, match="Incompatible"):
        modified_cost(10, "innate-attack", (selection(ACCURATE), selection(INACCURATE)))


def test_limited_enhancement_scopes_a_trusted_limitation_to_the_enhancement() -> None:
    limitation = selection("modifier:limitation:accessibility", option="only-in-darkness")
    approval = ModifierApproval(
        limitation.definition_id,
        "only-in-darkness",
        -40,
        frozenset({"innate-attack"}),
    )
    result = modified_cost(
        10,
        "innate-attack",
        (selection(AREA, limited_by=limitation),),
        (approval,),
    )
    assert result.components[0].percent == 30
    assert result.final_cost == 13


def test_unapproved_or_wrongly_scoped_custom_values_are_rejected() -> None:
    custom = selection("modifier:limitation:accessibility", option="only-in-darkness")
    with pytest.raises(ValidationError, match="exact campaign approval"):
        modified_cost(10, "innate-attack", (custom,))
    approval = ModifierApproval(
        custom.definition_id, "only-in-darkness", -40, frozenset({"advantage"})
    )
    with pytest.raises(ValidationError, match="does not allow"):
        modified_cost(10, "innate-attack", (custom,), (approval,))


def test_gadget_loss_and_unique_dependency_are_explicit() -> None:
    facts = GadgetConstruction(
        damage_resistance=5,
        size_modifier=-3,
        theft_method="quick-contest",
        unique=True,
    )
    modifiers = (
        selection(BREAKABLE, gadget=facts),
        selection(UNIQUE),
    )
    assert modified_cost(50, "advantage", modifiers).final_cost == 23
    assert gadget_available(modifiers, GadgetState())
    assert not gadget_available(modifiers, GadgetState(broken=True))
    with pytest.raises(ValidationError, match="Unique requires"):
        modified_cost(50, "advantage", (selection(UNIQUE),))
    # A stolen-only gadget is another valid loss route.
    stolen = selection(STOLEN, gadget=facts)
    assert modified_cost(50, "advantage", (stolen,)).final_cost == 35


def test_modifier_runtime_changes_the_typed_attack_receipt() -> None:
    profile = AttackProfile(
        accuracy=3,
        max_range=100,
        armor_divisor=Decimal(1),
        fatigue_cost=3,
        activation_seconds=4,
    )
    receipt = apply_attack_modifiers(
        profile,
        "innate-attack",
        (
            selection(ACCURATE, level=2),
            selection(INCREASED_RANGE, level=2),
            selection("modifier:enhancement:armor-divisor", option="5"),
            selection("modifier:enhancement:incendiary-inc"),
            selection("modifier:enhancement:reduced-fatigue-cost", level=2),
            selection("modifier:enhancement:reduced-time", level=1),
        ),
    )
    assert receipt.profile_id == PROFILE
    assert receipt.original == profile
    assert receipt.modified == AttackProfile(
        accuracy=5,
        max_range=500,
        armor_divisor=Decimal(5),
        fatigue_cost=1,
        activation_seconds=2,
        damage_tags=("incendiary",),
    )


def test_alternative_attacks_are_composed_definitions_not_name_conditions() -> None:
    alternatives = AlternativeAbilities(
        (
            AbilityDefinition(
                "burning-bolt",
                "innate-attack",
                20,
                (selection(ACCURATE, level=2),),
            ),
            AbilityDefinition("freezing-ray", "innate-attack", 10),
        )
    )
    # 20 + 10% = 22; the 10-point alternative contributes one fifth.
    assert alternatives.cost() == 24
