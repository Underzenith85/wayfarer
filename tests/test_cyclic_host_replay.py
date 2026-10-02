"""Recorded seeds/instants reexecute actual private Cyclic service transactions."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played
from test_cyclic_host import expose_command, prepare, stop_command

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.health.cyclic_host_state import (
    BeginCyclicProcedure,
    CompleteCyclicProcedure,
    CyclicHostCommand,
    CyclicPolicy,
    CyclicProcedure,
)
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("case", ["circumstance", "procedure", "exposure"])
async def test_restart_and_seed_only_reexecution_compare_complete_state_and_private_receipts(
    tmp_path: Path,
    backend: str,
    case: str,
) -> None:
    cid, play, occurrence = await prepare(
        tmp_path,
        backend,
        policy=CyclicPolicy(
            condition="wash",
            procedure=CyclicProcedure(
                seconds=2,
                check="dx",
                consume_definition_id="medicine",
            ),
        )
        if case == "procedure"
        else None,
        contagious=case == "exposure",
        interval=86400 if case == "exposure" else 10,
        incubation=3600 if case == "exposure" else 86400,
    )
    initial = await play.store.read(cid)
    play.rng = secrets
    service = CyclicService(play)
    command: CyclicHostCommand
    if case == "circumstance":
        await play.execute(
            cid,
            Wait(id="ordinary-nine", actor_id="b", expected_revision=1, ticks=9),
            principal_id="b",
        )
        command = stop_command(occurrence, 2)
    elif case == "procedure":
        started = BeginCyclicProcedure(
            id="begin",
            actor_id="b",
            expected_revision=1,
            occurrence_id=occurrence,
            performer_id="b",
            item_id="dose",
            location_id="room",
            reason="Observed the approved treatment begin",
        )
        await service.execute(cid, started, principal_id="gm")
        await play.execute(
            cid,
            Wait(id="ordinary-two", actor_id="b", expected_revision=2, ticks=2),
            principal_id="b",
        )
        command = CompleteCyclicProcedure(
            id="complete",
            actor_id="b",
            expected_revision=3,
            occurrence_id=occurrence,
            procedure_id=started.id,
            location_id="room",
            uninterrupted=True,
            reason="Observed uninterrupted completion after two actual seconds",
        )
    else:
        command = expose_command(occurrence).model_copy(
            update={
                "early_incubation_resolution": "defer-to-daily-check",
            }
        )
    result = await service.execute(cid, command, principal_id="gm")
    saved = await play.store.read(cid)
    records = await played(play.store, cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert all(check.folded and check.reexecuted for check in checks)
    assert replayed == saved
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await restarted.store.read(cid) == saved == await restarted.store.replay(cid)
    assert await CyclicService(restarted).execute(cid, command, principal_id="gm") == result
    assert len(await played(restarted.store, cid)) == len(records)
