"""B551 is a ranged body-target rule, not an area/object or Malediction modifier."""

from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_area_attacks import grenade, launch
from test_composed_attack_host import declare, fixture
from test_composed_attacks import pick
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.traits.composed_records import ResistComposedAttack
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_area_point_does_not_query_prone_body_exposure(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="prone")
    point = GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    await launch(cid, play, point, [3, 3, 3])
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert blast.aim_point == point and blast.attack_dice == (3, 3, 3)
    assert blast.direct_actor_id is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_carried_object_target_does_not_borrow_owners_posture_modifier(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12, size_modifier=0),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="prone")
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="b",
        target_item_id="sword-b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3, 3])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="object-response",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    # B400's held weapon penalty is -4, independently of its owner's pose.
    assert result.injury and result.injury.attack.effective_target == 9
    assert result.injury.attack.outcome.succeeded
    final = play._load(await play.store.read(cid))
    assert next(pool.current for pool in final.resources.pools if pool.id == "hp:b") == 10
    item = next(item for item in final.resources.items if item.id == "sword-b")
    assert item.condition and item.condition.hp == 9
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_malediction_ignores_ordinary_ranged_target_posture(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        modifiers=(pick("enhancement:malediction", option="1"),),
        kind="tox",
        levels=1,
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="prone")
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    play.rng = RecordedDice([2, 2, 2, 3])
    result = await ComposedAttackService(play).execute(
        cid,
        ResistComposedAttack(
            id="resist",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=incoming.id,
            resist=False,
        ),
        principal_id="bob",
    )
    assert result.attack and result.attack.injury
    assert result.attack.checks[0].effective_target == 8
    assert result.attack.injury.injury == 3 and play.rng.exhausted()
