"""Original-genesis registered Rooted commands and canonical deadline settlement."""

import secrets
from pathlib import Path

import pytest
from support.rooted_feet import fixture, observe, revision
from support.runtime import played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    CastRootedFeet,
    active_effect,
    effects,
)
from wayfarer.orchestration.rooted_feet import RootedFeetService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_original_genesis_cast_deadline_and_full_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    await observe(play, cid)
    play.rng = secrets
    play.seeds = lambda: f"{2:064x}"
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
    state = play._load(await play.store.read(cid))
    assert active_effect(state.resources, "b") is not None
    for seconds in (59, 1):
        await play.execute(
            cid,
            Wait(
                id="wait-" + str(seconds),
                actor_id="b",
                expected_revision=await revision(play, cid),
                ticks=seconds,
            ),
            principal_id="b",
        )
        state = play._load(await play.store.read(cid))
        assert (active_effect(state.resources, "b") is not None) == (seconds == 59)
        assert effects(state.resources)["root"].status == ("active" if seconds == 59 else "expired")
    saved = await play.store.read(cid)
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
