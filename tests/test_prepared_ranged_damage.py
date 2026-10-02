"""B373/B378: each projectile's damage has its own immediate consequence boundary."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import turn
from test_opponent_attack_inventory import fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.ranged.damage_records import (
    PreparedRangedDamage,
    RangedDamageStage,
)
from wayfarer.engine.simulation.combat.ranged.resolution import advance_ranged_damage, resolve
from wayfarer.engine.simulation.equipment.catalog import RangedMode


@pytest.mark.parametrize("secret", [False, True])
async def test_burst_stops_before_each_damage_dependent_injury_without_spending_ammo_twice(
    tmp_path: Path, secret: bool
) -> None:
    cid, play = await fixture(tmp_path, "sqlite", ranged=True)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    before = play._load(await play.store.read(cid))
    weapon = mode(play.rules_context, before, "a", "sword-a", "ranged")
    assert isinstance(weapon, RangedMode)
    play.rng = RecordedDice((3, 3, 4) + (() if secret else (1,)))
    first = resolve(
        play.rules_context,
        before,
        before.encounters[0],
        weapon,
        "none",
        None,
        second_defense=None,
        second_item_id=None,
        prepare_damage=True,
        secret_damage=secret,
    )
    assert isinstance(first, RangedDamageStage) and play.rng.exhausted()
    assert first.preparation.progress.index == 0 and first.preparation.context.impacts == 3
    assert first.preparation.original == (None if secret else (1,))
    assert next(p.current for p in first.state.resources.pools if p.id == "hp:b") == 10
    assert not first.state.resources.ammunition_loads
    captured = PreparedRangedDamage.model_validate_json(first.preparation.model_dump_json())
    # The selected first6 injury has a major-wound HT check before the next original.
    play.rng = RecordedDice((2, 2, 2) + (() if secret else (2,)))
    second = advance_ranged_damage(
        play.rules_context,
        first.state,
        first.encounter,
        captured.context,
        captured.progress,
        secret=secret,
        selected_damage=(6,),
    )
    assert isinstance(second, RangedDamageStage) and second.preparation.progress.index == 1
    assert next(p.current for p in second.state.resources.pools if p.id == "hp:b") == 4
    assert second.preparation.progress.damages == (6,) and play.rng.exhausted()
    play.rng = RecordedDice(() if secret else (1,))
    third = advance_ranged_damage(
        play.rules_context,
        second.state,
        second.encounter,
        second.preparation.context,
        second.preparation.progress,
        secret=secret,
        selected_damage=(3,),
    )
    assert isinstance(third, RangedDamageStage) and third.preparation.progress.index == 2
    assert next(p.current for p in third.state.resources.pools if p.id == "hp:b") == 1
    assert third.preparation.progress.damages == (6, 3) and play.rng.exhausted()
    play.rng = RecordedDice(())
    final = advance_ranged_damage(
        play.rules_context,
        third.state,
        third.encounter,
        third.preparation.context,
        third.preparation.progress,
        secret=secret,
        selected_damage=(2,),
    )
    assert not isinstance(final, RangedDamageStage)
    after, _, injury = final
    assert injury.attack.dice == (3, 3, 4) and injury.damage_dice == (6, 3, 2)
    assert injury.per_hit_damage == (6, 3, 2) and injury.injury == 11 and injury.hp_after == -1
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == -1
    assert not after.resources.ammunition_loads and play.rng.exhausted()
