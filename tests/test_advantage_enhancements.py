"""Independent executable fixtures for issue #682's B102-B109 enhancements."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from wayfarer.engine.rules.traits.modifiers import (
    ACCURATE,
    AREA,
    CONE,
    INCREASED_RANGE,
    JET,
    MELEE,
    MODIFIER_INDEX,
    RAPID_FIRE,
    AttackProfile,
    EnhancementParameters,
    ModifierApproval,
    ModifierSelection,
    apply_ability_modifiers,
    apply_attack_modifiers,
    modified_cost,
)
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def pick(
    identifier: str,
    *,
    level: int = 1,
    option: str | None = None,
    parameters: EnhancementParameters | None = None,
) -> ModifierSelection:
    return ModifierSelection(
        definition_id=identifier,
        level=level,
        option=option,
        parameters=parameters,
    )


def approve(
    selection: ModifierSelection, percent: int, subject: str = "innate-attack"
) -> ModifierApproval:
    return ModifierApproval(
        selection.definition_id,
        selection.option or "",
        percent,
        frozenset({subject}),  # type: ignore[arg-type]
    )


def test_source_derived_fixture_binds_all_42_remaining_rows_to_runtime_contracts() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/gurps/advantage-enhancements.json").read_text())
    assert fixture["profile"] == "gurps-basic-set-4e-2004"
    assert len(fixture["rows"]) == 42
    assert len({row[0] for row in fixture["rows"]}) == 42
    for identifier, page, hook in fixture["rows"]:
        definition = MODIFIER_INDEX[identifier]
        assert (definition.page, definition.runtime_hook) == (page, hook)
    assert not MODIFIER_INDEX["modifier:enhancement:damage-modifiers"].selectable


def test_area_persistence_mobility_duration_selection_and_rigid_wall_compose() -> None:
    selections = (
        pick("modifier:enhancement:wall", option="rigid"),
        pick("modifier:enhancement:mobile", level=2),
        pick("modifier:enhancement:extended-duration", option="3x"),
        pick("modifier:enhancement:selective-area"),
        pick("modifier:enhancement:persistent"),
        pick(AREA, level=2),
    )
    receipt = apply_attack_modifiers(
        AttackProfile(damage_kind="crushing"), "innate-attack", selections
    )
    assert receipt.modified.area_radius == 4
    assert receipt.modified.duration_seconds == 30
    assert receipt.modified.mobile_move == 2
    assert receipt.modified.selective_area
    assert receipt.modified.wall == "rigid"
    assert (receipt.modified.wall_dr_per_die, receipt.modified.wall_hp_per_die) == (
        3,
        Decimal("0.5"),
    )
    assert receipt.applied_modifier_ids[0] == AREA


def test_targeting_shape_aura_and_ranged_defaults_are_typed() -> None:
    target = apply_attack_modifiers(
        AttackProfile(),
        "innate-attack",
        (
            pick("modifier:enhancement:affects-insubstantial"),
            pick("modifier:enhancement:affects-substantial"),
        ),
    ).modified
    assert target.affects_substantial and target.affects_insubstantial
    assert target.usable_while_insubstantial

    aura = apply_attack_modifiers(
        AttackProfile(),
        "innate-attack",
        (pick("modifier:enhancement:aura"), pick(MELEE, option="reach-c")),
    ).modified
    assert aura.aura and not aura.is_ranged and aura.max_range == 0

    ranged = apply_ability_modifiers(
        AttackProfile(is_ranged=False, max_range=0, half_damage_range=0, accuracy=0),
        "advantage",
        (pick(INCREASED_RANGE, level=2), pick("modifier:enhancement:ranged")),
    ).modified
    assert (ranged.half_damage_range, ranged.max_range, ranged.accuracy) == (10, 500, 3)


def test_cone_cosmic_and_named_permissions_cannot_be_generic_bypasses() -> None:
    cone = pick(CONE, option="width-3", parameters=EnhancementParameters(width_yards=3))
    assert modified_cost(10, "innate-attack", (cone,), (approve(cone, 80),)).final_cost == 18
    assert (
        apply_attack_modifiers(
            AttackProfile(), "innate-attack", (cone,), (approve(cone, 80),)
        ).modified.cone_width_yards
        == 3
    )

    cosmic = pick(
        "modifier:enhancement:cosmic",
        option="irresistible-attack",
        parameters=EnhancementParameters(cosmic_effect="irresistible-attack"),
    )
    assert (
        apply_attack_modifiers(
            AttackProfile(), "innate-attack", (cosmic,), (approve(cosmic, 300),)
        ).modified.cosmic_effect
        == "irresistible-attack"
    )
    with pytest.raises(ValidationError, match="Cosmic approval percentage"):
        modified_cost(10, "innate-attack", (cosmic,), (approve(cosmic, 50),))


def test_damage_area_and_signature_consequences_are_not_anonymous_tags() -> None:
    damage = apply_attack_modifiers(
        AttackProfile(damage_kind="burning"),
        "innate-attack",
        (
            pick("modifier:enhancement:double-blunt-trauma-dbt"),
            pick("modifier:enhancement:explosion-exp", level=2),
            pick("modifier:enhancement:fragmentation-frag", level=3),
            pick("modifier:enhancement:surge-sur"),
            pick("modifier:enhancement:low-signature"),
        ),
    ).modified
    assert damage.blunt_trauma_multiplier == 2
    assert (damage.explosion_divisor, damage.fragmentation_dice) == (2, 3)
    assert damage.surge and damage.signature == "low"
    knockback = apply_attack_modifiers(
        AttackProfile(damage_kind="cutting"),
        "innate-attack",
        (
            pick("modifier:enhancement:double-knockback-dkb"),
            pick("modifier:enhancement:incendiary-inc"),
        ),
    ).modified
    assert knockback.knockback_multiplier == 2
    assert knockback.damage_tags == ("incendiary",)


def test_scheduled_cyclic_delay_drifting_and_hazard_facts_are_replayable() -> None:
    cyclic = pick(
        "modifier:enhancement:cyclic",
        option="ten-seconds-three-cycles",
        parameters=EnhancementParameters(
            interval_seconds=10,
            cycles=3,
            contagious="mild",
            stop_condition="wash off acid",
            damage_kind="corrosion",
        ),
    )
    delay = pick(
        "modifier:enhancement:delay",
        option="fixed",
        parameters=EnhancementParameters(delay_seconds=5),
    )
    receipt = apply_attack_modifiers(
        AttackProfile(damage_kind="corrosion"),
        "innate-attack",
        (pick("modifier:enhancement:drifting"), cyclic, delay),
        (approve(cyclic, 120), approve(delay, 0)),
    )
    assert (receipt.modified.cyclic_interval_seconds, receipt.modified.cyclic_cycles) == (10, 3)
    assert receipt.modified.cyclic_stop_condition == "wash off acid"
    assert receipt.modified.contagious == "mild"
    assert receipt.modified.delay_seconds == 5 and receipt.modified.drifting

    hazard = pick(
        "modifier:enhancement:hazard",
        option="starvation",
        parameters=EnhancementParameters(hazard="starvation", damage_kind="fatigue"),
    )
    result = apply_attack_modifiers(
        AttackProfile(damage_kind="fatigue"), "innate-attack", (hazard,), (approve(hazard, 40),)
    )
    assert result.modified.hazard == "starvation"


def test_guidance_link_malediction_rate_and_overhead_have_exact_runtime_facts() -> None:
    homing = pick(
        "modifier:enhancement:homing",
        option="vision-acc-3",
        parameters=EnhancementParameters(homing_sense="vision"),
    )
    assert (
        apply_attack_modifiers(
            AttackProfile(), "innate-attack", (homing,), (approve(homing, 50),)
        ).modified.guidance
        == "homing"
    )
    assert (
        apply_attack_modifiers(
            AttackProfile(), "innate-attack", (pick("modifier:enhancement:guided"),)
        ).modified.guidance
        == "guided"
    )

    link = pick(
        "modifier:enhancement:link",
        option="selectable",
        parameters=EnhancementParameters(linked_ability_id="binding-web"),
    )
    linked = apply_attack_modifiers(AttackProfile(), "innate-attack", (link,)).modified
    assert linked.link_selectable and linked.linked_ability_id == "binding-web"

    malediction = pick("modifier:enhancement:malediction", option="2")
    maledicted = apply_attack_modifiers(AttackProfile(), "innate-attack", (malediction,)).modified
    assert maledicted.malediction_range == "speed-range"

    rapid = pick(
        RAPID_FIRE,
        option="rof-8-15",
        parameters=EnhancementParameters(rate_of_fire=12, selective_fire=True),
    )
    projected = apply_attack_modifiers(
        AttackProfile(),
        "innate-attack",
        (pick("modifier:enhancement:overhead"), rapid),
    ).modified
    assert (projected.rate_of_fire, projected.selective_fire, projected.overhead) == (
        12,
        True,
        True,
    )


def test_resources_timing_resistance_and_environment_are_projected() -> None:
    changed = apply_ability_modifiers(
        AttackProfile(
            damage_kind="toxic",
            fatigue_cost=3,
            activation_seconds=8,
            resistance_attribute="ht",
        ),
        "advantage",
        (
            pick("modifier:enhancement:reduced-fatigue-cost", level=2),
            pick("modifier:enhancement:reduced-time", level=2),
        ),
    ).modified
    assert (changed.fatigue_cost, changed.activation_seconds) == (1, 2)
    resisted = apply_attack_modifiers(
        AttackProfile(resistance_attribute="ht"),
        "affliction",
        (
            pick(
                "modifier:enhancement:based-on-different-attribute",
                parameters=EnhancementParameters(resistance_attribute="will"),
            ),
        ),
    ).modified
    assert resisted.resistance_attribute == "will"

    radiation = pick(
        "modifier:enhancement:radiation-rad",
        option="toxic",
        parameters=EnhancementParameters(damage_kind="toxic"),
    )
    environmental = apply_attack_modifiers(
        AttackProfile(damage_kind="toxic"),
        "innate-attack",
        (radiation, pick("modifier:enhancement:underwater")),
    ).modified
    assert environmental.radiation_mode == "instead-of-damage"
    assert environmental.underwater_range_divisor == 10


def test_selectivity_symptoms_variable_damage_and_jet_are_explicit() -> None:
    symptom = pick(
        "modifier:enhancement:symptoms",
        option="blindness-at-half-hp",
        parameters=EnhancementParameters(
            symptom="blindness", symptom_threshold="one-half", damage_kind="toxic"
        ),
    )
    variable = pick(
        "modifier:enhancement:variable",
        parameters=EnhancementParameters(damage_fraction=Decimal("0.5")),
    )
    result = apply_attack_modifiers(
        AttackProfile(damage_kind="toxic"),
        "innate-attack",
        (symptom, variable, pick(JET)),
        (approve(symptom, 100),),
    ).modified
    assert (result.symptom, result.symptom_threshold) == ("blindness", "one-half")
    assert result.variable_damage_fraction == Decimal("0.5") and result.jet

    selectable = pick(
        "modifier:enhancement:selectivity",
        parameters=EnhancementParameters(disabled_enhancements=(ACCURATE,)),
    )
    switched = apply_attack_modifiers(
        AttackProfile(accuracy=0), "innate-attack", (selectable, pick(ACCURATE))
    ).modified
    assert switched.switchable_enhancements
    assert switched.disabled_enhancements == (ACCURATE,)
    assert switched.accuracy == 0

    enabled = apply_attack_modifiers(
        AttackProfile(accuracy=0), "innate-attack", (pick(ACCURATE),)
    ).modified
    assert enabled.accuracy == 1


def test_selectivity_removes_multiple_effects_before_stable_composition() -> None:
    selectivity = pick(
        "modifier:enhancement:selectivity",
        parameters=EnhancementParameters(
            disabled_enhancements=(ACCURATE, INCREASED_RANGE, RAPID_FIRE)
        ),
    )
    rapid_fire = pick(
        RAPID_FIRE,
        option="rof-4-7",
        parameters=EnhancementParameters(rate_of_fire=5, selective_fire=True),
    )
    selections = (
        rapid_fire,
        pick(INCREASED_RANGE, level=2),
        selectivity,
        pick(ACCURATE, level=3),
        pick("modifier:enhancement:overhead"),
    )
    receipt = apply_attack_modifiers(AttackProfile(), "innate-attack", selections)

    assert receipt.modified.accuracy == 0
    assert receipt.modified.max_range == 100
    assert receipt.modified.rate_of_fire == 1
    assert not receipt.modified.selective_fire
    assert receipt.modified.overhead
    assert receipt.modified.disabled_enhancements == (
        ACCURATE,
        INCREASED_RANGE,
        RAPID_FIRE,
    )
    assert ACCURATE not in receipt.applied_modifier_ids
    assert INCREASED_RANGE not in receipt.applied_modifier_ids
    assert RAPID_FIRE not in receipt.applied_modifier_ids

    cost = modified_cost(10, "innate-attack", selections)
    assert {component.definition_id for component in cost.components} == {
        selection.definition_id for selection in selections
    }


def test_selectivity_rejects_unpurchased_limitations_and_itself() -> None:
    for disabled in (
        (ACCURATE,),
        ("modifier:limitation:reduced-range",),
        ("modifier:enhancement:selectivity",),
    ):
        selectivity = pick(
            "modifier:enhancement:selectivity",
            parameters=EnhancementParameters(disabled_enhancements=disabled),
        )
        selections: tuple[ModifierSelection, ...] = (selectivity,)
        if disabled[0].startswith("modifier:limitation"):
            selections += (pick(disabled[0], level=1),)
        with pytest.raises(ValidationError, match="Selectivity must name selected enhancements"):
            apply_attack_modifiers(AttackProfile(), "innate-attack", selections)


def test_invalid_subjects_combinations_parameters_and_levels_fail_closed() -> None:
    with pytest.raises(ValidationError, match="grouping"):
        modified_cost(10, "innate-attack", (pick("modifier:enhancement:damage-modifiers"),))
    with pytest.raises(ValidationError, match="reach C"):
        modified_cost(
            10,
            "innate-attack",
            (pick("modifier:enhancement:aura"), pick(MELEE, option="reach-1-2")),
        )
    with pytest.raises(ValidationError, match="Rapid Fire rate"):
        modified_cost(
            10,
            "innate-attack",
            (pick(RAPID_FIRE, option="rof-2", parameters=EnhancementParameters(rate_of_fire=12)),),
        )
    with pytest.raises(ValidationError, match="crushing or burning"):
        apply_attack_modifiers(
            AttackProfile(damage_kind="toxic"),
            "innate-attack",
            (pick("modifier:enhancement:explosion-exp"),),
        )
    with pytest.raises(ValidationError, match="unsupported runtime parameters"):
        modified_cost(
            10,
            "innate-attack",
            (pick(ACCURATE, parameters=EnhancementParameters(width_yards=3)),),
        )
