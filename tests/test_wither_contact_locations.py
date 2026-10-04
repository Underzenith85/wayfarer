"""Actual non-arm hits spend Wither; successful ordinary defenses retain it."""

from pathlib import Path
from typing import Literal

import pytest
from support.wither_limb import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.wither_spell_state import casts, contact_results, read_contact
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService


async def prepare_location_contact(
    play: PlayService, cid: str, location: Literal["torso", "right-arm"]
) -> None:
    await cast(play, cid)
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="fight-start",
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
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-thrust",
            target_id="b",
            hit_location=location,
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None and read_contact(state.resources, pending.id) is not None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("defense", ["none", "dodge", "parry"])
async def test_actual_torso_or_successful_defense_has_no_wither_roll(
    tmp_path: Path, backend: str, defense: Literal["none", "dodge", "parry"]
) -> None:
    cid, play, _ = await fixture(
        tmp_path, backend, defender_item="staff" if defense == "parry" else None
    )
    await prepare_location_contact(play, cid, "torso" if defense == "none" else "right-arm")
    play.rng = RecordedDice((2, 3, 3, 1) if defense == "none" else (2, 3, 3, 2, 2, 3))
    response = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense=defense,
            item_id="defender-implement" if defense == "parry" else None,
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert result.outcome == ("no-effect" if defense == "none" else "held")
    assert not result.triggered and result.contact_check is None and result.resistance_check is None
    assert result.dice == () and result.injury == 0 and result.injury_checks == ()
    assert result.lasting_id is None and not disabled(state.resources, "b")
    assert casts(state.resources)["wither"].status == ("spent" if defense == "none" else "held")
    assert response.injury is not None
    assert response.injury.injury == (1 if defense == "none" else 0)
    if defense != "none":
        assert response.injury.defense is not None and response.injury.defense.outcome.succeeded
    assert next(p for p in state.resources.pools if p.id == "hp:b").current == (
        9 if defense == "none" else 10
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_armor_stops_staff_but_not_separate_wither_packet(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, defender_arm_armor=True)
    await prepare_location_contact(play, cid, "right-arm")
    play.rng = RecordedDice((2, 3, 3, 1, 3, 3, 3, 4, 4, 4, 1, 3, 3, 3))
    response = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    assert response.injury is not None and response.injury.injury == 0
    assert response.injury.resistance == 2
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert result.outcome == "withered" and result.dice == (1,)
    assert result.hp_before == 10 and result.hp_after == 9 and result.injury == 1
    assert result.injury_check_reasons == ("major-wound",) and len(result.injury_checks) == 1
    assert "right-arm" in disabled(state.resources, "b")
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury is not None
    lasting = next(i for i in hp.injury.lasting_injuries if i.id == result.lasting_id)
    assert lasting.duration == "permanent" and lasting.recovery_at is None
    assert await play.store.read(cid) == await play.store.replay(cid)
