"""Original genesis, actual producers, complete retry/restart and public discovery."""

import json
from pathlib import Path

import pytest
from support.detect_magic import complete, fixture, start, work
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.simulation.magic.detect_magic_state import CompleteDetectMagic
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("carrier", ["permanent", "temporary", "mundane"])
@pytest.mark.parametrize("seed", [14, 30])
async def test_original_seed_real_producer_outcome_reload_retry_and_actor_private_projection(
    tmp_path: Path,
    backend: str,
    carrier: str,
    seed: int,
) -> None:
    cid, play, initial = await fixture(tmp_path, backend, carrier)
    actor = "c"
    principal = "cora"
    await start(play, cid, carrier=carrier)
    await work(play, cid, actor=actor)
    prior = play._load(await play.store.read(cid))
    command = CompleteDetectMagic(
        id="detection-complete",
        actor_id=actor,
        expected_revision=prior.revision,
        cast_id="detection",
    )
    result = await complete(play, cid, seed, actor=actor)
    final = await play.store.read(cid)
    restarted = type(play)(play.store, play.engine, rng=play.rng)
    assert (
        await DetectMagicService(restarted).execute(cid, command, principal_id=principal) == result
    )
    assert await play.store.read(cid) == final
    runtime = build_runtime(restarted)
    for name in ("campaign", "stream"):
        view = await runtime.project(name, cid, principal_id=principal)
        visible = json.dumps(view.get("magic_detections", ()))
        assert (
            "identity" not in visible
            and "binding_id" not in visible
            and "project_id" not in visible
        )
        if seed == 14:
            values = validation.sequence(validation.decode(visible))
            assert len(values) == 1 and validation.mapping(values[0])["magical"] is (
                carrier != "mundane"
            )
        else:
            assert not view.get("magic_detections")
        for other in (
            m.principal_id for m in prior.members if m.role != "gm" and actor not in m.actor_ids
        ):
            assert "magic_detections" not in await runtime.project(name, cid, principal_id=other)
    state = play._load(final)
    assert final == await play.store.replay(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
