"""Original approved genesis and actual casting producer to secret identification."""

from pathlib import Path

import pytest
from support.identify_spell import fixture, producer, revision
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    IdentifySubject,
    ObserveIdentifySpellSubject,
    ReportIdentifySpell,
    projection,
    secret_result,
)
from wayfarer.orchestration.identify_spell import IdentifySpellService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("finished", [True, False])
async def test_actual_haste_completed_or_current_cast_identification(
    tmp_path: Path, backend: str, finished: bool
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    await producer(play, cid, finish=finished)
    service = IdentifySpellService(play)
    await service.execute(
        cid,
        ObserveIdentifySpellSubject(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=IdentifySubject(id="subject", caster_id="c", subject_id="b"),
        ),
        principal_id="gm",
    )
    before = play._load(await play.store.read(cid))
    command = CastIdentifySpell(
        id="identify",
        actor_id="c",
        expected_revision=before.revision,
        cast_id="identify",
        subject_id="subject",
    )
    play.seeds = lambda: f"{1:064x}"
    await build_runtime(play).submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    result = await service.execute(cid, command, principal_id="cora")
    state = play._load(await play.store.read(cid))
    assert result.model_dump() == {"command_id": "identify", "outcome": "finished"}
    secret = secret_result(state.resources, "identify")
    assert (
        secret is not None
        and secret.check.effective_target == 10
        and secret.check.outcome.succeeded
    )
    assert state.resources.game_time == before.resources.game_time + 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 8
    assert secret.descriptions == ("Haste",) and len(secret.spells) == 1
    assert secret.spells[0].status == ("completed" if finished else "casting")
    assert not projection(state.resources, ("c",))
    await service.execute(
        cid,
        ReportIdentifySpell(
            id="finding", actor_id="gm", expected_revision=state.revision, cast_id="identify"
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert projection(state.resources, ("c",)) == (
        {"actor_id": "c", "subject_id": "b", "descriptions": ["Haste"]},
    )
    assert not projection(state.resources, ("a", "b"))
    final = await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(r.folded and r.reexecuted for r in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
