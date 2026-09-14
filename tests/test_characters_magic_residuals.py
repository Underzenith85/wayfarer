"""Independent source-derived fixtures for Characters B237, B239-241, and B256."""

import json
from pathlib import Path
from typing import Literal

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import (
    HeldSpell,
    MagicStaff,
    blocking_interruption,
    dispose_held_spell,
    effect_limit,
    long_distance_modifier,
    spell_class_procedure,
    staff_casting_benefit,
    staff_custody_transition,
    supernatural_counter_applies,
    supernatural_interaction_route,
    validate_effect_levels,
)
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SpellCommand,
    SpellContext,
    SpellEffect,
    SpellEvent,
    SpellResult,
    apply_spell,
)
from wayfarer.engine.simulation.resources import Pool, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_owns_the_six_exact_section_rows() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/gurps/characters-magic-residuals.json").read_text()
    )
    assert fixture["profile"] == "gurps-basic-set-4e-2004"
    assert [row[0] for row in fixture["rows"]] == [
        "section:characters:b237:limits-on-effect",
        "section:characters:b239:spell-classes",
        "section:characters:b240:magic-staffs",
        "section:characters:b241:dissipating-held-melee-and-missile-spells",
        "section:characters:b241:long-distance-modifiers",
        "section:characters:b256:psionics-and-magic",
    ]


def test_finite_effect_levels_use_the_greater_of_source_limit_and_magery() -> None:
    assert effect_limit(4, 2).maximum_levels == 4
    assert effect_limit(4, 10).maximum_levels == 10
    assert effect_limit(None, 3).maximum_levels is None
    assert validate_effect_levels(10, 4, 10) == 10
    with pytest.raises(ValidationError, match="finite Magery limit"):
        validate_effect_levels(11, 4, 10)


def test_every_spell_class_has_explicit_attack_defense_range_and_roll_contracts() -> None:
    regular = spell_class_procedure("regular")
    area = spell_class_procedure("area")
    melee = spell_class_procedure("melee", resisted=True)
    penetrating_melee = spell_class_procedure("melee", ignores_armor=True)
    missile = spell_class_procedure("missile")
    blocking = spell_class_procedure("blocking")
    information = spell_class_procedure("information")

    assert (regular.distance_rule, regular.physical_barriers_apply) == ("regular", False)
    assert area.distance_rule == "area-edge"
    assert (melee.attack_roll, melee.resistance_contest) == ("melee", True)
    assert penetrating_melee.legal_defenses == ("dodge", "weapon-parry")
    assert missile.legal_defenses == ("dodge", "block")
    assert missile.physical_barriers_apply
    assert blocking.attack_roll == "defense"
    assert (information.secret_roll, information.momentary) == (True, True)
    assert blocking_interruption().model_dump() == {
        "preparing_spell_lost": True,
        "held_melee_retained": True,
        "held_missile_retained": True,
        "held_missile_can_enlarge": False,
    }


def test_staff_forms_construction_and_all_three_casting_benefits() -> None:
    wand = MagicStaff(item_id="wand", form="wand", material="bone", length_yards=0)
    short = MagicStaff(item_id="short", form="short-staff", material="coral", length_yards=1)
    full = MagicStaff(item_id="full", form="full-staff", material="wood", length_yards=2)
    assert (wand.reach, wand.weapon_skills) == ("C", ("knife", "main-gauche"))
    assert (short.reach, short.weapon_skills) == ("1", ("shortsword", "smallsword"))
    assert (full.reach, full.weapon_skills) == ("2", ("staff", "two-handed-sword"))
    assert staff_casting_benefit(full, 7, touching_with_staff=True).effective_distance_yards == 0
    assert (
        staff_casting_benefit(full, 7, pointing_declared_at_start=True).effective_distance_yards
        == 5
    )
    assert staff_casting_benefit(full, 7).effective_distance_yards == 7
    with pytest.raises(ValueError, match="form and reach"):
        MagicStaff(item_id="bad", form="wand", material="ivory", length_yards=2)


def test_staff_custody_and_free_action_disposal_cover_every_consequence() -> None:
    melee = HeldSpell(cast_id="shock", actor_id="mage", spell_class="melee", staff_item_id="staff")
    assert staff_custody_transition(melee, caster_still_holds=False).outcome == "dissipated"
    contact = staff_custody_transition(
        melee,
        caster_still_holds=True,
        jointly_held_by="thief",
        casters_turn=True,
        wrench_attempt=True,
    )
    assert (contact.outcome, contact.target_id) == ("contact-triggered", "thief")
    assert dispose_held_spell(melee, "dissipate").outcome == "dissipated"
    with pytest.raises(ValidationError, match="Only a held Missile"):
        dispose_held_spell(melee, "drop")

    fireball = HeldSpell(
        cast_id="fireball",
        actor_id="mage",
        spell_class="missile",
        energy=3,
        explosive=True,
        burning=True,
    )
    dropped = dispose_held_spell(fireball, "drop")
    assert dropped.free_action
    assert (dropped.ground_damage_dice, dropped.affects_caster, dropped.ignition_possible) == (
        3,
        True,
        True,
    )
    harmless = dispose_held_spell(fireball, "dissipate")
    assert (harmless.ground_damage_dice, harmless.affects_caster, harmless.ignition_possible) == (
        0,
        False,
        False,
    )


def held_fireball_state() -> tuple[ResourceState, SpellContext]:
    effect = SpellEffect(
        cast_id="fireball",
        actor_id="mage",
        target_id="target",
        spell_id="fireball",
        build_revision="approved",
        phase="active",
        started_at=0,
        ready_at=1,
        skill=15,
        cost=3,
        maintenance=0,
        hp_at_start=10,
        energy=3,
    )
    state = ResourceState(
        revision=7,
        game_time=2,
        pools=(
            Pool(
                id="hp:mage",
                current=10,
                maximum=10,
                injury=InjuryStatus(profile_id=PROFILE),
            ),
            Pool(
                id="fp:mage",
                current=7,
                maximum=10,
                fatigue=FatigueStatus(profile_id=PROFILE),
            ),
        ),
        events=(
            ResourceEvent(
                id="spell:fixture",
                at=1,
                target_id="mage",
                kind=SpellEvent(
                    effect=effect, result=SpellResult(outcome="active")
                ).model_dump_json(),
            ),
        ),
    )
    context = SpellContext(
        profile_id=PROFILE,
        build_revision="approved",
        skill=15,
        magery=3,
        target_id="target",
    )
    return state, context


@pytest.mark.parametrize("kind,outcome", [("dissipate", "dissipated"), ("drop", "dropped")])
def test_held_disposal_is_cas_safe_and_replayable(
    kind: Literal["dissipate", "drop"], outcome: Literal["dissipated", "dropped"]
) -> None:
    state, context = held_fireball_state()
    command = SpellCommand(
        id="dispose",
        actor_id="mage",
        expected_revision=7,
        kind=kind,
        spell_id="fireball",
        cast_id="fireball",
    )
    updated, result = apply_spell(state, command, context, rng=RecordedDice(()), system=True)
    assert (updated.revision, result.outcome, result.energy_spent) == (8, outcome, 0)
    assert result.held_disposition is not None and result.held_disposition.free_action
    replay, repeated = apply_spell(updated, command, context, rng=RecordedDice(()), system=True)
    assert replay == updated and repeated == result
    with pytest.raises(ConflictError, match="ID reused"):
        apply_spell(
            updated,
            command.model_copy(update={"kind": "drop" if kind == "dissipate" else "dissipate"}),
            context,
            rng=RecordedDice(()),
            system=True,
        )


def test_complete_long_distance_table_and_factor_of_ten_progression() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/gurps/characters-magic-residuals.json").read_text()
    )
    for yards, penalty in fixture["long_distance_yards"]:
        assert long_distance_modifier(yards) == penalty
    assert long_distance_modifier(201) == -1
    assert long_distance_modifier(881) == -2
    assert long_distance_modifier(1_760_001) == -10
    with pytest.raises(ValidationError, match="nonnegative"):
        long_distance_modifier(-1)


def test_psi_and_magic_counters_stay_separate_but_results_use_shared_paths() -> None:
    assert supernatural_counter_applies("psi", "psi")
    assert supernatural_counter_applies("magic", "magic")
    assert not supernatural_counter_applies("psi", "magic")
    assert not supernatural_counter_applies("magic", "psi")
    for family in ("psi", "magic"):
        assert supernatural_interaction_route(family, "fire") == "hazard:fire"
        assert supernatural_interaction_route(family, "healing") == "health:healing"
        assert supernatural_interaction_route(family, "mind") == "resistance:mind-shield"
        assert supernatural_interaction_route(family, "detection") == "supernatural:" + family
        assert supernatural_interaction_route(family, "neutralization") == "supernatural:" + family
