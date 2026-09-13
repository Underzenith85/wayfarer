"""Campaigns fourth-printing B413-B415 area, scatter and explosion fixtures."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, ResolveWeaponExplosion
from wayfarer.orchestration.play import PlayService


def grenade() -> RangedMode:
    return weapon(thrown=True).model_copy(
        update={"firearm": FirearmSpec(technology_level=6, action="grenade", fuse_seconds=1)}
    )


async def launch(
    cid: str,
    play: PlayService,
    point: GroundPosition,
    dice: list[int],
    *,
    squared: bool = False,
) -> None:
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        area_aim_point=point,
        scatter_squared=squared,
    )
    play.rng = RecordedDice(dice)
    await defend(cid, play, "b")


async def test_declared_area_point_hits_without_target_defense_and_consumes_once(
    tmp_path: Path,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
    )
    point = GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    await launch(cid, play, point, [3, 3, 3])
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert blast.center == point and blast.aim_point == point
    assert blast.attack_range == 1 and blast.attack_dice == (3, 3, 3)
    assert blast.direct_actor_id is None and blast.scatter_direction is None
    assert not any(i.id == "sword-a" for i in state.resources.items)
    assert sum(i.id == "sword-a" for i in state.resources.expended_items) == 1
    assert next(i for i in state.resources.expended_items if i.id == "sword-a").ground == point
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(("aim_x", "squared", "expected_distance"), [(5, False, 1), (10, True, 5)])
async def test_scatter_records_range_direction_and_capped_distance(
    tmp_path: Path, aim_x: int, squared: bool, expected_distance: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
        battlefield=Battlefield(id="dock", location_id="dock", width=20, height=12),
    )
    point = GroundPosition(encounter_id="fight", geometry="grid", x=aim_x, y=0)
    # At 5 yards this misses by one. At 10 yards, squared scatter is 3^2,
    # capped to half the declared attack range.
    await launch(cid, play, point, [5, 5, 6, 3], squared=squared)
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert blast.attack_range == aim_x and blast.attack_dice == (5, 5, 6)
    assert blast.scatter_direction == 3 and blast.scatter_distance == expected_distance
    assert blast.center == point.model_copy(update={"x": aim_x + expected_distance})
    assert (
        next(i for i in state.resources.expended_items if i.id == "sword-a").ground == blast.center
    )


async def test_bad_area_geometry_is_rejected_before_attack_dice(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="outside the battlefield"):
        await turn(
            cid,
            play,
            "a",
            "attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=50, y=50),
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize(("mode", "expected_hp"), [("contact", 4), ("internal", 7)])
async def test_contact_and_internal_modes_use_authoritative_center(
    tmp_path: Path, mode: str, expected_hp: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
    )
    point = GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    await launch(cid, play, point, [3, 3, 3])
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "c", "do_nothing")
    state = play._load(await play.store.read(cid))
    command = ResolveWeaponExplosion(
        id="contact",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blasts(state.resources)[0].id,
        responses=tuple(
            BlastResponse(actor_id=actor, cover_dr=0, size_modifier=0) for actor in ("a", "b", "c")
        ),
        object_cover={},
        environment="air",
        contact_actor_id="b" if mode == "contact" else None,
        internal_actor_id="b" if mode == "internal" else None,
    )
    play.rng = RecordedDice([1] * 20)
    await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == expected_hp
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 20
    assert sum(i.id == "sword-a" for i in state.resources.expended_items) == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_barrier_objects_and_multiple_distances_share_blast_transaction(
    tmp_path: Path,
) -> None:
    durability = ObjectProfile(construction="unliving", hp=100, dr=100, ht=10, size_modifier=0)
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=2),
        durability=durability,
        placements=(
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            Placement(actor_id="c", position=GridPoint(x=3, y=0), facing="west"),
        ),
        battlefield=Battlefield(id="dock", location_id="dock", width=8, height=8),
    )
    point = GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    await launch(cid, play, point, [3, 3, 3])
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "c", "do_nothing")
    state = play._load(await play.store.read(cid))
    objects = {"sword-a": 0, "sword-b": 2, "shield-b": 2, "sword-c": 0}
    command = ResolveWeaponExplosion(
        id="barrier-blast",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blasts(state.resources)[0].id,
        responses=tuple(
            BlastResponse(
                actor_id=actor,
                cover_dr=2 if actor == "b" else 0,
                covered_locations=("torso",) if actor == "b" else (),
                size_modifier=0,
            )
            for actor in ("a", "b", "c")
        ),
        object_cover=objects,
        environment="air",
    )
    play.rng = RecordedDice([2, 2, 2, 2] * 6)
    await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    state = play._load(await play.store.read(cid))
    assert len(state.resources.object_results) == 4
    assert all(result.injury == 0 for result in state.resources.object_results)
    assert await play.store.read(cid) == await play.store.replay(cid)
