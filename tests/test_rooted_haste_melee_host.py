"""Actual incoming weapon attacks consume paid personal Haste before Rooted floor."""

from pathlib import Path
from typing import Literal

import pytest
from support.rooted_feet import revision
from support.rooted_haste import fixture, prepare_composition
from support.runtime import build_play

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("order", ["before", "after"])
@pytest.mark.parametrize("energy", [1, 3])
async def test_actual_weapon_dodge_composes_current_haste_and_retains_fixed_feet(
    tmp_path: Path, backend: str, order: Literal["before", "after"], energy: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, subject_dx=14, subject_ht=10)
    await prepare_composition(play, cid, energy=energy, order=order)
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
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="incoming",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
    before = play._load(await play.store.read(cid))
    target_before = next(p for p in before.encounters[0].participants if p.actor_id == "b")
    play.rng = RecordedDice((2, 3, 3, 2, 2, 2) + ((1,) if energy == 1 else ()))
    response = ChooseDefense(
        id="dodge",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="dodge",
    )
    receipt = await combat.execute(cid, response, principal_id="b")
    assert play.rng.exhausted()
    assert receipt.injury is not None and receipt.injury.defense is not None
    assert receipt.injury.attack.effective_target == 13
    assert receipt.injury.defense.effective_target == (9 + energy) // 2
    assert receipt.injury.defense.outcome.succeeded == (energy == 3)
    after = play._load(await play.store.read(cid))
    target_after = next(p for p in after.encounters[0].participants if p.actor_id == "b")
    assert target_after.position == target_before.position
    assert active_effect(after.resources, "b") is not None
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == (
        7 if energy == 1 else 10
    )
    play.rng = RecordedDice(())
    assert await combat.execute(cid, response, principal_id="b") == receipt
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == receipt
    assert await play.store.read(cid) == await play.store.replay(cid)
