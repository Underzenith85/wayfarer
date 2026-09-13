"""Independent Campaigns fourth-printing B398-B406 cases for #510."""

from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from test_gurps_melee import choice, setup

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.special_melee import (
    attack_from_above,
    chink_penalty,
    dirty_trick,
    grapple_size_bonus,
    improvised_weapon,
    liquid_in_face,
    size_reach,
    special_unarmed,
    special_weapon_defense,
)
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.combat.combat_height import defense_height
from wayfarer.engine.simulation.combat.special_melee import approved_size_modifier
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_SOURCE,
    Armor,
    Damage,
    EquipmentProfile,
    MeleeMode,
    Parry,
)
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn


def test_b400_chink_penalty_is_derived_from_location_and_damage_kind() -> None:
    assert chink_penalty("torso", "imp", tight_beam=False) == -8
    assert chink_penalty(None, "pi", tight_beam=False) == -8
    assert chink_penalty("right-arm", "burn", tight_beam=True) == -10
    with pytest.raises(ValidationError, match="tight-beam"):
        chink_penalty("torso", "burn", tight_beam=False)
    with pytest.raises(ValidationError, match="declared"):
        chink_penalty("random", "imp", tight_beam=False)


async def test_b400_chink_attack_uses_worn_armor_and_halves_dr(tmp_path: Path) -> None:
    armor = EquipmentProfile(
        definition_id="equipment:test-mail",
        provenance=LITE_SOURCE,
        weight_millipounds=10000,
        price=100,
        technology_level=3,
        slot="armor",
        armor=Armor(locations=("torso",), dr=4),
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        melee_modes=(
            MeleeMode(
                id="thrust",
                skill_id="skill:broadsword",
                minimum_st=10,
                damage=Damage(basis="thrust", adds=1, damage_type="imp"),
                reach=(1,),
                parry=Parry(),
            ),
        ),
        extra_equipment=(armor,),
        extra_items=(
            Item(
                id="mail-b",
                definition_id=armor.definition_id,
                owner_id="b",
                equipped=True,
            ),
        ),
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="chink",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="thrust",
            target_id="b",
            hit_location="torso",
            armor_chink=True,
        ),
        principal_id="a",
    )
    play.rng = RecordedDice([1, 1, 2, 3, 3, 3, 4])
    result = await CombatService(play).execute(cid, choice(), principal_id="b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 5  # skill 13, complete chink penalty -8
    assert result.injury.resistance == 2
    assert result.injury.basic_damage == 3
    assert result.injury.injury == 2  # (3 - floor(4/2)) * impaling 2


async def test_b401_pull_strength_and_turn_blade_are_committed_subdual(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="flat",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
            strike_strength=9,
            subdual_mode="flat",
        ),
        principal_id="a",
    )
    play.rng = RecordedDice([3, 3, 3, 4])
    result = await CombatService(play).execute(cid, choice(), principal_id="b")
    assert result.injury is not None
    assert result.injury.basic_damage == 4  # ST 9 swing plus the weapon's printed add
    assert result.injury.injury == 4  # crushing, not the cutting mode's x1.5


async def test_b400_weapon_hit_dispatches_through_object_damage(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12),
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="weapon-hit",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
            target_item_id="sword-b",
        ),
        principal_id="a",
    )
    play.rng = RecordedDice([3, 3, 3, 4])
    result = await CombatService(play).execute(cid, choice(), principal_id="b")
    state = play._load(await play.store.read(cid))
    assert result.injury is not None and result.injury.injury == 0
    damage = state.resources.object_results[-1]
    assert (damage.item_id, damage.injury, damage.condition.hp) == ("sword-b", 4, 8)


def test_b402_positive_sm_reach_is_read_from_approved_build() -> None:
    approved = SimpleNamespace(purchases=(Purchase(definition_id="trait:size-modifier", amount=2),))
    compiled = cast(ValidatedBuild, approved)
    assert approved_size_modifier(compiled) == 2
    reaches = size_reach((1,), approved_size_modifier(compiled))
    assert reaches == (1, 2)
    assert defense_height(Fraction(0), Fraction(2), reach=max(reaches)) == -1


def test_b402_b406_special_melee_tables_cover_declared_procedures() -> None:
    assert size_reach((0,), 1) == (0, 1)
    assert size_reach((2, 3), 3) == (2, 3, 4, 5)
    assert grapple_size_bonus(4, 1) == 3

    hidden = attack_from_above(looking_up=False, stealth_won=True, drop_yards=3)
    assert (hidden.vision_modifier, hidden.defense_modifier, hidden.attack_modifier) == (
        -2,
        None,
        -2,
    )
    assert hidden.falling_damage

    assert special_unarmed("elbow-strike", location="face").attack_modifier == -3
    assert special_unarmed("knee-strike", front_grapple=True).defense_modifier == -2
    lethal = special_unarmed("lethal-strike", location="vitals")
    assert (lethal.attack_modifier, lethal.damage_add, lethal.damage_type) == (-2, -1, "pi")
    assert special_unarmed("neck-snap", location="neck").contest_st_modifier == -4
    assert special_unarmed("trample", attacker_sm=3, defender_sm=0).large_area

    improvised = improvised_weapon(clumsiness=2, short_or_light=True)
    assert (improvised.attack_modifier, improvised.parry_modifier) == (-2, -2)
    assert (improvised.minimum_st_add, improvised.damage_add) == (2, -1)
    assert dirty_trick(novelty="repeated", mode="quick-contest").attacker_modifier == -2
    with pytest.raises(ValidationError, match="repeatable"):
        dirty_trick(novelty="known", mode="attacker")
    assert liquid_in_face(critical_hit=False, defended=False, will_succeeded=False) == (0, -2)
    assert special_weapon_defense("flail") == (-4, -2)
    assert special_weapon_defense("kusari") == (-4, -2)
