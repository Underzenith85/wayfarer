"""Protocol diagnostics on genuine accepted casts, not full-genesis replay proof."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.melee_spell import cast, fixture
from support.runtime import build_runtime, played
from test_actions import campaign

from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("generation", [True, 1.0], ids=["boolean", "float"])
async def test_actual_melee_spell_record_rejects_noninteger_generation_atomically(
    tmp_path: Path, backend: str, generation: bool | float
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    completed = await cast(play, cid)
    record = next(r for r in await played(play.store, cid) if r.command_id == completed.id)
    assert record.reexecutable and record.command_input is not None
    payload = validation.mapping(replay_payload(record.command_input))
    assert payload["operation"] == "melee-spell"
    assert type(payload["generation"]) is int and payload["generation"] == 1
    corrupted = {**payload, "generation": generation}
    encoded = json.dumps(corrupted, sort_keys=True)
    candidate = replace(
        record,
        command_input=encoded,
        payload_hash=payload_digest({"input": encoded}),
    )
    # Preserve a valid input digest so rejection reaches the registered family
    # generation guard rather than the generic corruption check.
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="captured generation"):
        await execute_recorded(play, candidate)
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_cast_private_genesis_and_actor_projection_boundaries(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    state = play._load(await play.store.read(cid))
    receipt = next(e for e in state.resources.events if e.id.startswith("melee-spell:receipt:"))
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            state.resources.model_copy(update={"revision": 0, "events": (receipt,)}),
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    runtime = build_runtime(play)
    for name in ("campaign", "stream"):
        owner = await runtime.project(name, cid, principal_id="alice")
        rows = validation.sequence(validation.decode(json.dumps(owner["melee_spell_findings"])))
        charge = validation.mapping(rows[0])
        assert charge["actor_id"] == "a" and charge["status"] == "held"
        assert set(charge) == {"cast_id", "actor_id", "status", "carrier", "energy", "paid_fp"}
        assert "melee-spell:" not in json.dumps(owner)
        for principal in ("bob", "watcher"):
            other = await runtime.project(name, cid, principal_id=principal)
            assert "melee_spell_findings" not in other
            assert "melee-spell:" not in json.dumps(other)
