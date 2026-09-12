"""Campaigns fourth-printing B409 spraying-fire integration."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.combat import (
    GridPoint,
    Placement,
    RangedSituation,
    SprayTarget,
)
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService


def automatic_weapon() -> RangedMode:
    return RangedMode(
        id="spray",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="fixed", dice=1, damage_type="cr"),
        accuracy=3,
        range_basis="yards",
        maximum_range=100,
        half_damage_range=50,
        shots=20,
        reload_seconds=2,
        rate_of_fire=15,
        recoil=2,
        bulk=-3,
        ammunition_id="equipment:ammo",
    )


async def spraying_fixture(
    tmp_path: Path, placements: tuple[Placement, ...] | None = None
) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=automatic_weapon(),
        third_actor=True,
        ranged_scene=(
            RangedSituation(attacker_id="a", defender_id="b", distance_yards=3),
            RangedSituation(attacker_id="a", defender_id="c", distance_yards=3),
        ),
        placements=placements
        or (
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=3, y=0), facing="west"),
            Placement(actor_id="c", position=GridPoint(x=2, y=1), facing="west"),
        ),
    )
    for _ in range(2):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="spray",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
        await turn(cid, play, "c", "do_nothing")
    return cid, play


async def test_spraying_fire_persists_separate_attacks_and_traversal_cost(
    tmp_path: Path,
) -> None:
    cid, play = await spraying_fixture(tmp_path)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="spray-three",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="spray",
            shots=5,
            spray_targets=(SprayTarget(target_id="c", shots=4),),
        ),
        authenticated_actor_id="a",
    )
    play.rng = RecordedDice([3, 3, 4, 1, 1])
    first = await defend(cid, play, "b")
    assert first.injury is not None
    assert (first.injury.attack.effective_target, first.injury.shots_fired, first.injury.hits) == (
        13,
        5,
        2,
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert (pending.defender_id, pending.shots, pending.spray_recoil_penalty) == ("c", 4, 1)
    assert pending.traversal_shots == 1

    play.rng = RecordedDice([3, 3, 4, 1])
    defense = ChooseDefense(
        id="spray-second-defense",
        actor_id="c",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    second = await CombatService(play).execute(cid, defense, authenticated_actor_id="c")
    assert await CombatService(play).execute(cid, defense, authenticated_actor_id="c") == second
    assert second.injury is not None
    assert (
        second.injury.attack.effective_target,
        second.injury.shots_fired,
        second.injury.hits,
    ) == (
        12,
        4,
        1,
    )
    final = play._load(await play.store.read(cid))
    assert not final.resources.ammunition_loads
    assert "ammo-a" not in {item.id for item in final.resources.items}
    assert final.encounters[0].current_actor_id == "b"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_invalid_spraying_fire_rejects_before_dice_or_mutation(tmp_path: Path) -> None:
    cid, play = await spraying_fixture(tmp_path)
    before = await play.store.read(cid)
    state = play._load(before)
    with pytest.raises(ValidationError, match="exceed weapon RoF"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="too-many",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                target_id="b",
                mode_id="spray",
                shots=10,
                spray_targets=(SprayTarget(target_id="c", shots=10),),
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before


async def test_spraying_fire_rejects_targets_outside_thirty_degree_arc(tmp_path: Path) -> None:
    cid, play = await spraying_fixture(
        tmp_path,
        (
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=3, y=0), facing="west"),
            Placement(actor_id="c", position=GridPoint(x=0, y=3), facing="south"),
        ),
    )
    before = await play.store.read(cid)
    state = play._load(before)
    with pytest.raises(ValidationError, match="30-degree angle"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="wide-sweep",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                target_id="b",
                mode_id="spray",
                shots=5,
                spray_targets=(SprayTarget(target_id="c", shots=4),),
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before
