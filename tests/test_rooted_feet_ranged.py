"""Actual canonical thrown attack retains its purchased ranged skill while rooted."""

from pathlib import Path

import pytest
from support.rooted_feet import fixture, observe, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet, active_effect
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.rooted_feet import RootedFeetService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("rooted", [False, True])
async def test_actual_canonical_hatchet_skill_unchanged(
    tmp_path: Path, backend: str, rooted: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, ranged_weapon=True)
    if rooted:
        await observe(play, cid, target="a")
        play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
        await RootedFeetService(play).execute(
            cid,
            CastRootedFeet(
                id="root",
                actor_id="c",
                expected_revision=await revision(play, cid),
                cast_id="root",
                subject_id="subject",
            ),
            principal_id="cora",
        )
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
            ranged_situations=(
                RangedSituation(
                    attacker_id="a",
                    defender_id="b",
                    distance_yards=1,
                    speed_yards_per_second=0,
                    size_modifier=0,
                ),
            ),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "a":
        actor = state.encounters[0].current_actor_id
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="wait-" + actor,
                actor_id=actor,
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    assert (active_effect(state.resources, "a") is not None) == rooted
    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 1, 1, 1))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="throw",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="hatchet-a",
            mode_id="thrown",
            target_id="b",
        ),
        principal_id="a",
    )
    result = await combat.execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury is not None and result.injury.attack.effective_target == 13
    assert result.injury.attack.base_target == 13
    final = play._load(await play.store.read(cid))
    assert not any(i.id == "hatchet-a" and i.owner_id == "a" for i in final.resources.items)
    assert (active_effect(final.resources, "a") is not None) == rooted
    assert await play.store.read(cid) == await play.store.replay(cid)
