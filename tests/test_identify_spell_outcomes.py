"""Secret Information outcomes, physical authority, exact retry and rollback."""

import json
from pathlib import Path

import pytest
from support.identify_spell import fixture, observe, producer, revision
from support.runtime import build_play, build_runtime
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    ReportIdentifySpell,
    secret_result,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.identify_spell import IdentifySpellService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "seed,descriptions,success",
    [(1119, None, True), (48, None, False), (30, ("An illusion",), False)],
)
async def test_secret_cost_outcomes_privacy_false_shape_and_daily(
    tmp_path: Path, backend: str, seed: int, descriptions: tuple[str, ...] | None, success: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await producer(play, cid)
    await observe(play, cid, unfamiliar=True)
    command = CastIdentifySpell(
        id="identify",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="identify",
        subject_id="subject",
    )
    before = play._load(await play.store.read(cid))
    play.seeds = lambda: f"{seed:064x}"
    await build_runtime(play).submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    service = IdentifySpellService(play)
    assert (await service.execute(cid, command, principal_id="cora")).model_dump() == {
        "command_id": "identify",
        "outcome": "finished",
    }
    final = await play.store.read(cid)
    state = play._load(final)
    secret = secret_result(state.resources, "identify")
    assert secret is not None and secret.check.outcome.succeeded is success
    assert secret.descriptions == ("A movement enhancement",)
    assert state.resources.game_time == before.resources.game_time + 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 8
    report = ReportIdentifySpell(
        id="finding",
        actor_id="gm",
        expected_revision=state.revision,
        cast_id="identify",
        descriptions=descriptions,
    )
    for principal in ("cora", "alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await service.execute(cid, report, principal_id=principal)
    assert await play.store.read(cid) == final
    await build_runtime(play).submit_json(cid, report.model_dump(mode="json"), principal_id="gm")
    expected = ["A movement enhancement"] if success else list(descriptions or ())
    for name in ("campaign", "stream"):
        view = await build_runtime(play).project(name, cid, principal_id="cora")
        assert json.loads(json.dumps(view["spell_identifications"])) == [
            {"actor_id": "c", "subject_id": "b", "descriptions": expected}
        ]
        assert "truthful" not in json.dumps(view) and "identify-spell:" not in json.dumps(view)
        for principal in ("alice", "bob", "watcher"):
            assert "spell_identifications" not in await build_runtime(play).project(
                name, cid, principal_id=principal
            )
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="today"):
        await service.execute(
            cid,
            command.model_copy(
                update={
                    "id": "again",
                    "cast_id": "again",
                    "expected_revision": await revision(play, cid),
                }
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cast_authority_stale_cas_restart_and_fixed_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await producer(play, cid)
    await observe(play, cid)
    command = CastIdentifySpell(
        id="identify",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="identify",
        subject_id="subject",
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await IdentifySpellService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await IdentifySpellService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="cora"
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await IdentifySpellService(failing).execute(cid, command, principal_id="cora")
    assert (
        await play.store.read(cid) == saved
        and await play.store.history(cid) == history
        and await play.store.stream(cid) == stream
    )
    play.rng = RecordedDice((3, 3, 3))
    receipt = await IdentifySpellService(play).execute(cid, command, principal_id="cora")
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    restart = build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(()))
    assert await IdentifySpellService(restart).execute(cid, command, principal_id="cora") == receipt
    with pytest.raises(ConflictError):
        await IdentifySpellService(restart).execute(
            cid, command.model_copy(update={"subject_id": "changed"}), principal_id="cora"
        )
    assert await play.store.read(cid) == final
    assert isinstance(restart.rng, RecordedDice) and restart.rng.exhausted()
