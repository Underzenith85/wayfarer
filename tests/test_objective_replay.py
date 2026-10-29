"""Typed objective replay executes the original service without fresh providers."""

import secrets
from pathlib import Path
from typing import Literal, Never

import pytest
from support.runtime import played
from test_wave9 import goals, prepare

from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.events import EngineEvent
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.objectives import ObjectiveCommand, ObjectiveService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandRecord
from wayfarer.persistence.replay import verify_commands


def refuse_fresh() -> Never:
    raise AssertionError("Objective replay must use recorded entropy and command time")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,actor", [("evaluate_objectives", "a"), ("abandon_scenario", "gm")])
async def test_objectives_reexecute_original_seed_and_exact_events(
    tmp_path: Path,
    backend: str,
    kind: Literal["evaluate_objectives", "abandon_scenario"],
    actor: str,
) -> None:
    cid, play = await prepare(tmp_path / "source", objectives=goals(), backend=backend)
    play.rng = secrets
    before = await play.store.read(cid)
    state = play._load(before)
    result = await ObjectiveService(play).execute(
        cid,
        ObjectiveCommand(kind=kind, id="objective", actor_id=actor, expected_revision=0),
        principal_id=actor,
    )
    assert result.outcome == ("abandoned" if kind == "abandon_scenario" else "ongoing")
    saved = await play.store.read(cid)
    records = await played(play.store, cid)
    assert len(records) == 1 and records[0].entropy_seed is not None

    async def replay(image: Campaign, record: CommandRecord) -> tuple[Campaign, list[EngineEvent]]:
        # Clone the authentic beforeimage, never the saved result. This is the
        # same isolated per-command replay contract used by the campaign host.
        store = AsyncSQLiteStore(tmp_path / "verification.sqlite")
        await store.commit_genesis(image, command_id="verification", text="authentic beforeimage")
        clone = PlayService(store, play.engine, seeds=refuse_fresh, instants=refuse_fresh)
        await execute_recorded(clone, record)
        return await store.read(cid), [
            row.event for row in await store.stream(cid, after=image["revision"])
        ]

    final, checks = await verify_commands(
        before,
        records,
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=replay,
    )
    assert final == saved
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert checks[0].reason is None
    assert await play.store.replay(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("rejection", ["malformed", "principal", "player-abandon"])
async def test_objective_rejections_preserve_state_history_and_entropy(
    tmp_path: Path, backend: str, rejection: str
) -> None:
    cid, play = await prepare(tmp_path, objectives=goals(), backend=backend)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    events = await play.store.stream(cid)
    play.seeds = refuse_fresh
    play.instants = refuse_fresh
    kind = "abandon_scenario" if rejection == "player-abandon" else "evaluate_objectives"
    raw = {"kind": kind, "id": "refused", "actor_id": "a", "expected_revision": 0}
    if rejection == "malformed":
        raw["kind"] = "invented_objective"
    with pytest.raises((AuthorizationError, ValidationError)):
        await ObjectiveService(play).execute(
            cid, raw, principal_id="b" if rejection == "principal" else "a"
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events
