"""Actual rooted weapon attack and active defenses consume the derived scores."""

from pathlib import Path
from typing import Literal

import pytest
from support.rooted_feet import fixture, observe, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.rooted_feet import RootedFeetService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "rooted,defense,attack_score,defense_score",
    [("a", "none", 11, None), ("b", "parry", 13, 8), ("b", "dodge", 13, 4)],
)
async def test_real_weapon_attack_parry_and_plain_dodge(
    tmp_path: Path,
    backend: str,
    rooted: str,
    defense: Literal["none", "parry", "dodge"],
    attack_score: int,
    defense_score: int | None,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, combat_weapons=True)
    await observe(play, cid, target=rooted)
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
    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 1, 1, 1))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
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
            defense=defense,
            item_id="sword-b" if defense == "parry" else None,
        ),
        principal_id="b",
    )
    assert result.injury is not None
    assert result.injury.attack.effective_target == attack_score
    assert (
        result.injury.defense.effective_target if result.injury.defense else None
    ) == defense_score
    assert await play.store.read(cid) == await play.store.replay(cid)
