"""Actual registered bandaging retains source injury and reexecutes from genesis."""

from pathlib import Path

import pytest
from support.medical_replay import begin, finish, fixture, runtime
from support.runtime import build_play, played
from support.wither_limb import revision
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.medical_commands import recorded_command
from wayfarer.persistence.events import CommandInput
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_nondefault_bandage_original_genesis_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original, care = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.current == 7 and hp.injury is not None
    permanent = hp.injury.lasting_injuries
    started_at = state.resources.game_time
    await begin(play, cid, care)
    state = play._load(await play.store.read(cid))
    task = state.resources.recovery_tasks[-1]
    assert (
        task.id == "bandage" and task.kind == "bandage" and task.actor_id == task.target_id == "b"
    )
    assert task.due == started_at + 60 and not task.settled
    await runtime(play, care).submit_json(
        cid,
        Wait(
            id="care-wait", actor_id="b", expected_revision=await revision(play, cid), ticks=60
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    await finish(play, cid, care)
    saved = await play.store.read(cid)
    after = play._load(saved)
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert hp.current == 8 and hp.injury is not None
    assert hp.injury.lasting_injuries == permanent and "right-arm" in disabled(after.resources, "b")
    assert after.resources.game_time == started_at + 60
    assert after.resources.recovery_tasks[-1].settled
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        original,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=after.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert evidence and all(c.folded and c.reexecuted for c in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_existing_task_finish_reexecutes_without_care_callback(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, care = await fixture(tmp_path, backend)
    await begin(play, cid, care)
    await runtime(play, care).submit_json(
        cid,
        Wait(
            id="care-wait", actor_id="b", expected_revision=await revision(play, cid), ticks=60
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    # This is a genuine pre-Finish task checkpoint, not a substitute for Begin provenance.
    original_task = await play.store.read(cid)
    await finish(play, cid, care)
    saved = await play.store.read(cid)
    records = [r for r in await played(play.store, cid) if r.command_id == "bandage-finish"]
    row = records[0]
    finish_command = recorded_command(CommandInput(row.payload_hash, row.command_input))

    def refuse(*_args: object) -> CareEnvironment:
        raise AssertionError("Accepted Finish execution must not refresh care")

    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    await MedicalService(restarted, refuse).execute(cid, finish_command, principal_id="b")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    replayed, evidence = await verify_commands(
        original_task,
        records,
        [e for e in await play.store.stream(cid) if e.command_id == "bandage-finish"],
        configuration_digest=play._load(saved).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "finish-reexecute"),
    )
    # Replay's medical callback always raises; successful Finish proves it was never consulted.
    assert len(evidence) == 1 and evidence[0].folded and evidence[0].reexecuted
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)
