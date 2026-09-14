"""Golden source and persistence checks for issue #684."""

from fractions import Fraction
from pathlib import Path

import pytest
from test_gurps_melee import setup

from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import Damage, EquipmentProfile, MeleeMode, Parry
from wayfarer.engine.simulation.resources import Item
from wayfarer.orchestration.combat import CombatService


def entries() -> dict[str, EquipmentProfile]:
    return {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }


def melee_mode(item: str, mode_id: str) -> MeleeMode:
    selected = next(mode for mode in entries()[item].modes if mode.id == mode_id)
    assert isinstance(selected, MeleeMode)
    return selected


def test_pick_modes_lodge_and_recovery_state_is_serializable() -> None:
    for item, mode_id in (
        ("pick", "axe-mace-swing"),
        ("halberd", "polearm-pick"),
        ("scythe", "two-handed-axe-mace-pick"),
        ("warhammer", "two-handed-axe-mace-swing"),
    ):
        assert melee_mode(item, mode_id).can_stick
    lodged = Item(
        id="pick:1",
        definition_id="equipment:pick",
        owner_id="attacker",
        equipped=True,
        ready=False,
        stuck_target_id="defender",
        stuck_injury=7,
        stuck_damage_type="imp",
    )
    assert Item.model_validate_json(lodged.model_dump_json()) == lodged


def test_lance_mode_carries_mounted_charge_procedure() -> None:
    lance = melee_mode("lance", "lance-thrust")
    assert lance.mounted_lance
    assert lance.damage.damage_type == "imp"
    assert lance.reach == (4,)


def test_starred_reaches_require_persisted_ready_selection() -> None:
    expected = {
        "kusari": (1, 2, 3, 4),
        "glaive": (2, 3),
        "spear": (1,),
        "long-spear": (2, 3),
        "maul": (1, 2),
        "warhammer": (1, 2),
        "monowire-whip": (1, 2, 3, 4, 5, 6, 7),
    }
    for item, reach in expected.items():
        selected = next(
            mode
            for mode in entries()[item].modes
            if isinstance(mode, MeleeMode) and mode.reach_requires_ready
        )
        assert selected.reach == reach
    held = Item(
        id="spear:1",
        definition_id="equipment:spear",
        owner_id="actor",
        equipped=True,
        ready=True,
        melee_reach=1,
    )
    assert Item.model_validate_json(held.model_dump_json()).melee_reach == 1


@pytest.mark.asyncio
async def test_ready_reach_round_trips_through_combat_persistence(tmp_path: Path) -> None:
    adjustable = MeleeMode(
        id="swing",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="swing", adds=1, damage_type="cut"),
        reach=(1, 2),
        parry=Parry(),
        reach_requires_ready=True,
    )
    campaign_id, play = await setup(
        tmp_path,
        profile="gurps-basic-set-4e-2004",
        human=True,
        melee_modes=(adjustable,),
    )
    state = play._load(await play.store.read(campaign_id))
    await CombatService(play).execute(
        campaign_id,
        TakeCombatTurn(
            id="ready-reach",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="ready",
            item_id="sword-a",
            mode_id="swing",
            ready_hand="right-hand",
            ready_reach=1,
        ),
        principal_id="a",
    )
    restarted = play._load(await play.store.read(campaign_id))
    weapon = next(item for item in restarted.resources.items if item.id == "sword-a")
    assert weapon.melee_reach == 1


def test_cutlass_hilt_uses_punch_damage_modes() -> None:
    hilt = [
        mode
        for mode in entries()["cutlass"].modes
        if isinstance(mode, MeleeMode) and mode.punch_damage
    ]
    assert {mode.skill_id for mode in hilt} == {"skill:brawling", "skill:boxing", "skill:karate"}
    assert all(mode.minimum_st is None and mode.reach == (0,) for mode in hilt)


def test_superscience_rows_are_setting_authorized_inventory() -> None:
    current = entries()
    for item in ("force-sword", "monowire-whip", "force-shield"):
        assert current[item].technology_level == "superscience"
        assert current[item].inventory_spec().superscience
    force = current["force-shield"].shield
    assert force is not None
    assert current["force-shield"].legality_class == 3
    assert (force.defense_bonus, force.dr, force.hardened_dr, force.occupies_hand) == (
        3,
        100,
        True,
        False,
    )


def test_whip_and_chainsaw_rows_preserve_formula_values() -> None:
    current = entries()
    for yards in range(1, 8):
        row = current[f"whip-{yards}-yard"]
        mode = melee_mode(f"whip-{yards}-yard", "whip-swing")
        assert (row.price, row.weight_millipounds, mode.minimum_st) == (
            20 * yards,
            2000 * yards,
            5 + yards,
        )
        assert mode.reach == tuple(range(1, yards + 1))
        assert mode.long_reach_ready_turns == (2 if yards >= 3 else 1)
    saw = melee_mode("chainsaw", "two-handed-axe-mace-swing")
    assert saw.damage.bonus_dice == 1
    assert saw.ready_after_attack_below_st_multiple == Fraction(3, 2)


def test_shield_variants_preserve_offense_and_physical_rules() -> None:
    current = entries()
    base = current["medium-shield"]
    buckler = current["medium-buckler"]
    iron = current["iron-medium-shield"]
    riot = current["plastic-riot-medium-shield"]
    assert melee_mode("medium-shield", "shield-bash").shield_attack
    assert melee_mode("spiked-medium-shield", "shield-bash-spike").damage.adds == 1
    assert buckler.shield is not None
    assert (buckler.shield.skill_id, buckler.shield.buckler, buckler.shield.can_rush) == (
        "skill:shield-buckler",
        True,
        False,
    )
    assert (
        base.durability is not None and iron.durability is not None and riot.durability is not None
    )
    assert (iron.price, iron.weight_millipounds, iron.durability.dr, iron.durability.hp) == (
        base.price * 5,
        base.weight_millipounds * 2,
        base.durability.dr + 3,
        base.durability.hp * 2,
    )
    assert riot.weight_millipounds == base.weight_millipounds // 2
