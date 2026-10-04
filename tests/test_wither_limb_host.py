"""Manufactured Staff contact produces separate HP injury and permanent arm loss."""

from pathlib import Path

import pytest
from support.wither_limb import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.wither_spell_state import (
    WitherLimbContactResult,
    casts,
    contact_results,
    read_contact,
)
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService


async def prepare_contact(play: PlayService, cid: str) -> None:
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
            hit_location="right-arm",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None and read_contact(state.resources, pending.id) is not None


async def contact(play: PlayService, cid: str, *, magic_die: int = 1) -> WitherLimbContactResult:
    await prepare_contact(play, cid)
    service = CombatService(play)
    play.rng = RecordedDice((3, 3, 3, 1, 3, 3, 3, 4, 4, 4, magic_die, 3, 3, 3))
    response = await service.execute(
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
    assert response.injury is not None and response.injury.injury == 1
    state = play._load(await play.store.read(cid))
    return contact_results(state.resources)[0]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("magic_die", [1, 6])
async def test_actual_separate_magic_packet_forces_major_wound_and_permanent_arm(
    tmp_path: Path, backend: str, magic_die: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    result = await contact(play, cid, magic_die=magic_die)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert result.outcome == "withered" and result.dice == (magic_die,)
    assert result.hp_before == 9 and result.hp_after == 9 - magic_die and result.injury == magic_die
    assert hp.current == 9 - magic_die and hp.injury is not None
    assert result.location == "right-arm"
    lasting = next(i for i in hp.injury.lasting_injuries if i.id == result.lasting_id)
    assert (
        lasting.kind == "crippled"
        and lasting.duration == "permanent"
        and lasting.recovery_at is None
    )
    assert "right-arm" in disabled(state.resources, "b")
    assert result.injury_check_reasons == ("major-wound",) and len(result.injury_checks) == 1
    assert result.injury_checks[0].base_target == 10
    assert (
        casts(state.resources)["wither"].status == "spent"
        and casts(state.resources)["wither"].paid_fp == 4
    )
    assert await play.store.read(cid) == await play.store.replay(cid)
