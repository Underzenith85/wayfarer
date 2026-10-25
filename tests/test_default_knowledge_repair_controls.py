"""The exact repaired weapon scenarios still reject unknown unpaid defaults."""

from pathlib import Path

import pytest
from support.armoury_defaults import fixture as pistol_fixture
from support.armoury_defaults import revision
from support.runtime import build_play, open_store, seed_campaign
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon
from test_ranged_target_postures import shoot

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.default_knowledge import observations
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_literal_ranged_weapon_still_refuses_without_gm_familiarity(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        trained=False,
        ranged_fixture=True,
        ranged_mode=weapon(),
        ranged_scene=scene(2),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await load(cid, play)
    assert not observations(play._load(await play.store.read(cid)).resources)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="no trained or legal default"):
        await shoot(cid, play)
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_canonical_repair_pistol_still_refuses_without_gm_familiarity(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await pistol_fixture(
        tmp_path,
        backend,
        "firearm",
        "skill:engineer-small-arms",
        hp=6,
    )
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="pistol-check",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="pistol-check",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=1, y=2)),
            ),
            ranged_situations=(
                RangedSituation(attacker_id="b", defender_id="a", distance_yards=2),
            ),
        ),
        principal_id="gm",
    )
    await turn(cid, play, "a", "do_nothing", encounter_id="pistol-check")
    await turn(cid, play, "b", "ready", item_id="repair-target", encounter_id="pistol-check")
    await turn(cid, play, "a", "do_nothing", encounter_id="pistol-check")
    assert not observations(play._load(await play.store.read(cid)).resources)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="no trained or legal default"):
        await turn(
            cid,
            play,
            "b",
            "attack",
            item_id="repair-target",
            mode_id="shot",
            target_id="a",
            encounter_id="pistol-check",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and play.rng.exhausted()
