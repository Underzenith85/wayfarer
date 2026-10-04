"""Registered original hand casts deliver actual punches and independent magical HP."""

from pathlib import Path
from typing import Literal

import pytest
from support.hand_deathtouch import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.hand_melee_contact_state import contact_results
from wayfarer.engine.simulation.magic.hand_melee_spell_state import casts
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.play import PlayService


async def prepare_contact(
    play: PlayService,
    cid: str,
    *,
    energy: int = 1,
    hand: Literal["left-hand", "right-hand"] = "right-hand",
    skill: Literal["attribute:dx", "skill:brawling"] = "skill:brawling",
) -> None:
    await cast(play, cid, energy=energy, hand=hand)
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
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
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="punch",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            action="punch",
            enter_close_combat=True,
            target_id="b",
            hands=(hand,),
            skill=skill,
            location="torso",
        ),
        principal_id="a",
    )
    assert play._load(await play.store.read(cid)).encounters[0].pending_unarmed is not None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("energy", [1, 2, 3])
@pytest.mark.parametrize(
    "hand,skill,armor",
    [("right-hand", "skill:brawling", False), ("left-hand", "attribute:dx", True)],
)
async def test_registered_hand_punch_has_separate_armor_ignoring_packet(
    tmp_path: Path,
    backend: str,
    energy: int,
    hand: Literal["left-hand", "right-hand"],
    skill: Literal["attribute:dx", "skill:brawling"],
    armor: bool,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, defender="armor" if armor else "unarmed")
    play.rng = RecordedDice((3, 3, 3))
    await prepare_contact(play, cid, energy=energy, hand=hand, skill=skill)
    play.rng = RecordedDice((3, 3, 3, 4 if armor else 3) + (1,) * energy)
    response = await CombatService(play).execute(
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
    assert play.rng.exhausted(), (
        response.model_dump_json(),
        [
            r.model_dump_json()
            for r in contact_results(play._load(await play.store.read(cid)).resources)
        ],
    )
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert result.outcome == "discharged" and result.hand == hand and result.dice == (1,) * energy
    assert result.injury == energy and result.hp_after == result.hp_before - energy
    assert response.unarmed is not None
    assert response.unarmed.injury == (0 if armor else 1)
    assert result.hp_before == (10 if armor else 9)
    assert next(p for p in state.resources.pools if p.id == "hp:b").current == result.hp_after
    assert casts(state.resources)["hand"].credited_seconds == 1
    assert casts(state.resources)["hand"].completed_at == 1
    assert response.unarmed.checks[0].effective_target == (13 if skill == "skill:brawling" else 10)
    assert casts(state.resources)["hand"].status == "spent"
    assert casts(state.resources)["hand"].paid_fp == max(0, energy - 1)
    assert next(p for p in state.resources.pools if p.id == "fp:a").current == 10 - max(
        0, energy - 1
    )
    assert await play.store.read(cid) == await play.store.replay(cid)
