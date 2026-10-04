"""Actual opaque first aid uses captured care TL for time and healing."""

import secrets
from pathlib import Path
from typing import cast

import pytest
from support.medical_replay import choice, fixture
from support.runtime import build_runtime, played
from support.wither_limb import revision
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.orchestration.medical import CareEnvironment
from wayfarer.orchestration.medical_context import recorded
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trusted", [False, True])
async def test_actual_first_aid_default_and_nondefault_care(
    tmp_path: Path, backend: str, trusted: bool
) -> None:
    cid, play, original, care = await fixture(tmp_path, backend, first_aid=True)
    care.environment = CareEnvironment(technology_level=7)
    access = build_runtime(play, medical_environment=care if trusted else None)
    offered = await access.read(cid, principal_id="bob")
    started = play._load(await play.store.read(cid)).resources.game_time
    await access.submit_json(
        cid,
        {
            "id": "first-aid",
            "actor_id": "b",
            "expected_revision": await revision(play, cid),
            "kind": "gurps_recovery",
            "choice_id": cast(
                str,
                [
                    c
                    for c in cast(list[dict[str, object]], offered["gurps_recovery_choices"])
                    if c["kind"] == "first-aid"
                ][-1]["id"],
            ),
        },
        principal_id="bob",
    )
    task = play._load(await play.store.read(cid)).resources.recovery_tasks[-1]
    wound = next(
        e for e in play._load(await play.store.read(cid)).resources.events if e.id == task.wound_id
    )
    assert InjuryResult.model_validate_json(wound.kind).injury == 2
    assert task.technology_level == (7 if trusted else 8)
    assert task.due == started + (1200 if trusted else 600)
    await access.submit_json(
        cid,
        Wait(
            id="care-wait",
            actor_id="b",
            expected_revision=await revision(play, cid),
            ticks=task.due - started,
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    offered = await access.read(cid, principal_id="bob")
    play.rng = secrets
    play.seeds = lambda: f"{72:064x}"
    await access.submit_json(
        cid,
        {
            "id": "first-aid-finish",
            "actor_id": "b",
            "expected_revision": await revision(play, cid),
            "kind": "gurps_recovery",
            "choice_id": choice(offered, "finish-recovery"),
        },
        principal_id="bob",
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (8 if trusted else 9)
    captured_input = await play.store.command_input(cid, "first-aid")
    assert captured_input is not None
    _, captured = recorded(captured_input)
    assert captured is not None
    assert captured.source_kind == (
        "trusted-scenario-snapshot" if trusted else "configuration-default"
    )
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    saved = await play.store.read(cid)
    replayed, evidence = await verify_commands(
        original,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "first-aid-reexecute"),
    )
    assert evidence and all(e.folded and e.reexecuted for e in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)
