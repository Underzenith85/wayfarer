"""Actual authenticated Rooted transactions preserve atomic custody and retries."""

from pathlib import Path

import pytest
from support.rooted_feet import fixture, observe, revision
from support.runtime import build_play
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.rooted_feet import RootedFeetService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authority_stale_cas_restart_and_changed_retry(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await observe(play, cid)
    command = CastRootedFeet(
        id="root",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="root",
        subject_id="subject",
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await RootedFeetService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await RootedFeetService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="cora"
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3, 6, 6, 6)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await RootedFeetService(failing).execute(cid, command, principal_id="cora")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    receipt = await RootedFeetService(play).execute(cid, command, principal_id="cora")
    final = await play.store.read(cid)
    restart = build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(()))
    assert await RootedFeetService(restart).execute(cid, command, principal_id="cora") == receipt
    with pytest.raises(ConflictError):
        await RootedFeetService(restart).execute(
            cid, command.model_copy(update={"subject_id": "changed"}), principal_id="cora"
        )
    assert await play.store.read(cid) == final
    assert isinstance(restart.rng, RecordedDice) and restart.rng.exhausted()
