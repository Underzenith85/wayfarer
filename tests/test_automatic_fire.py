"""Campaigns B408 full-auto-only minimum burst integration."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.errors import ValidationError


def automatic_only() -> RangedMode:
    data = weapon().model_dump()
    data.update(
        damage=Damage(basis="fixed", dice=1, damage_type="pi"),
        rate_of_fire=8,
        minimum_shots_per_attack=2,
        firearm=FirearmSpec(technology_level=6, action="repeating"),
    )
    return RangedMode.model_validate(data)


async def test_full_auto_minimum_rejects_short_declared_burst(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=automatic_only(),
        ranged_scene=scene(),
        campaign_technology_level=6,
    )
    await load(cid, play)
    with pytest.raises(ValidationError, match="automatic-only minimum"):
        await turn(
            cid,
            play,
            "a",
            "attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            shots=1,
        )


async def test_full_auto_may_fire_all_of_a_short_remaining_load(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=automatic_only(),
        ranged_scene=scene(),
        campaign_technology_level=6,
    )
    await load(cid, play)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        shots=5,
    )
    play.rng = RecordedDice([4, 4, 4, 1, 1])
    first = await defend(cid, play, "b")
    assert first.injury is not None and first.injury.shots_fired == 5
    await turn(cid, play, "b", "do_nothing")
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        shots=1,
    )
    play.rng = RecordedDice([4, 4, 4, 1])
    final = await defend(cid, play, "b")
    assert final.injury is not None and final.injury.shots_fired == 1
    state = play._load(await play.store.read(cid))
    assert not state.resources.ammunition_loads
    assert await play.store.read(cid) == await play.store.replay(cid)
