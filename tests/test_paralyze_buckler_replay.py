"""Actual old/new captured contact policy reexecutes from the original Staff genesis."""

import secrets
from pathlib import Path

import pytest
from support.paralyze_buckler import cast, fixture, revision
from support.runtime import build_play, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.limb_spell_state import contact_results, read_contact
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
    generations,
)
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [True, False])
async def test_old_and_new_actual_buckler_contact_full_genesis_replay(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
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
    active = generations.ACTIVE
    if legacy:
        monkeypatch.setattr(generations, "ACTIVE", active - {"paralyze-buckler-drop"})
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
            hit_location="left-arm",
        ),
        principal_id="a",
    )
    monkeypatch.setattr(generations, "ACTIVE", active)
    play.rng = secrets
    play.seeds = lambda: f"{19:064x}"
    response = ChooseDefense(
        id="response",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    accepted = await service.execute(cid, response, principal_id="b")
    saved = await play.store.read(cid)
    state = play._load(saved)
    result = contact_results(state.resources)[0]
    assert result.outcome == "paralyzed", accepted.model_dump_json()
    assert result.dropped_item_ids == (() if legacy else ("defender-implement",))
    captured = read_contact(state.resources, result.pending_id)
    assert captured is not None and captured.generation == (2 if legacy else 4)
    assert ("generation" in captured.model_dump(mode="json")) is not legacy
    restarted = build_play(tmp_path / "restart", play.engine, store=play.store)
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == accepted
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(e.folded and e.reexecuted for e in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)
