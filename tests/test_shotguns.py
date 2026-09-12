"""Characters B279 rows and Campaigns B409 multiple-projectile attacks."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.ranged import multiple_projectile_attack
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_SOURCE,
    Armor,
    Damage,
    EquipmentProfile,
    MultipleProjectiles,
    RangedMode,
)
from wayfarer.engine.simulation.resources import Item


def shotgun() -> RangedMode:
    return weapon().model_copy(
        update={
            "damage": Damage(basis="fixed", dice=1, damage_type="pi"),
            "half_damage_range": 50,
            "rate_of_fire": 2,
            "recoil": 1,
            "multiple_projectiles": MultipleProjectiles(projectiles_per_shot=9),
        }
    )


@pytest.mark.parametrize(
    ("distance", "effective_rof", "damage_and_dr_multiplier"),
    [(4.999, 2, 4), (5, 18, 1)],
)
def test_b409_ten_percent_boundary(
    distance: float, effective_rof: int, damage_and_dr_multiplier: int
) -> None:
    assert multiple_projectile_attack(2, 9, distance, 50) == (
        effective_rof,
        damage_and_dr_multiplier,
    )


async def test_close_shotgun_multiplies_damage_but_spends_one_shell(tmp_path: Path) -> None:
    armor = EquipmentProfile(
        definition_id="equipment:test-armor",
        provenance=LITE_SOURCE,
        weight_millipounds=0,
        price=1,
        technology_level=1,
        slot="torso",
        armor=Armor(locations=("torso",), dr=2),
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=shotgun(),
        ranged_scene=scene(4),
        extra_equipment=(armor,),
        extra_items=(
            Item(id="armor-b", definition_id=armor.definition_id, owner_id="b", equipped=True),
        ),
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert (result.injury.shots_fired, result.injury.hits) == (1, 1)
    assert result.injury.per_hit_damage == (12,)
    assert result.injury.resistance == 8
    assert result.injury.per_hit_injury == (4,)
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 5
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_dispersed_shotgun_counts_pellets_but_spends_one_shell(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=shotgun(),
        ranged_scene=scene(5),
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 1, 1, 1, 1, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13
    assert (result.injury.shots_fired, result.injury.hits) == (1, 5)
    assert result.injury.per_hit_damage == (1, 1, 1, 1, 1)
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 5
    assert await play.store.read(cid) == await play.store.replay(cid)
