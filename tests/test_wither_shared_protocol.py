"""Private shared joins use an actual learned Wither and manufactured Staff."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_runtime, played
from support.wither_limb import fixture, revision

from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.magic.concentration import (
    require_idle_concentration,
    require_no_held_melee,
)
from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
from wayfarer.engine.simulation.magic.wither_spell_commands import CastWitherLimb
from wayfarer.engine.simulation.magic.wither_spell_state import casts
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest


async def start(play: PlayService, cid: str) -> CastWitherLimb:
    command = CastWitherLimb(
        id="wither-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="wither",
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id="alice"
    )
    return command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("generation", [True, 3.0, 1, 2, None])
async def test_real_start_rejects_rehashed_wrong_generation_before_rng(
    tmp_path: Path, backend: str, generation: bool | float | None
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    record = next(r for r in await played(play.store, cid) if r.command_id == "wither-start")
    payload = validation.mapping(replay_payload(record.command_input or "{}"))
    assert payload["generation"] == 3
    encoded = json.dumps({**payload, "generation": generation}, sort_keys=True)
    candidate = replace(
        record, command_input=encoded, payload_hash=payload_digest({"input": encoded})
    )
    saved = await play.store.read(cid)
    history = await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="captured generation"):
        await execute_recorded(play, candidate)
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_pending_wait_does_not_credit_concentration(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    state = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError, match="Wither"):
        require_idle_concentration(state.resources, "a")
    await build_runtime(play).submit_json(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=state.revision, ticks=1).model_dump(
            mode="json"
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert casts(state.resources)["wither"].status == "cancelled"
    assert casts(state.resources)["wither"].credited_seconds == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_held_projection_and_common_exclusion(tmp_path: Path, backend: str) -> None:
    cid, play, original = await fixture(tmp_path, backend)
    command = await start(play, cid)
    for operation in ("concentrate", "complete"):
        await build_runtime(play).submit_json(
            cid,
            command.model_copy(
                update={
                    "id": "wither-" + operation,
                    "operation": operation,
                    "expected_revision": await revision(play, cid),
                }
            ).model_dump(mode="json"),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    assert casts(state.resources)["wither"].status == "held"
    with pytest.raises(ConflictError, match="held Melee"):
        require_no_held_melee(state.resources, "a")
    from wayfarer.engine.simulation.magic.identify_spell_admission import _private_spell_producers

    with pytest.raises(ConflictError, match="unsupported private spell producer"):
        _private_spell_producers(state, "a")
    receipt = next(e for e in state.resources.events if e.id.startswith("wither-spell:receipt:"))
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            original,
            state.world,
            state.resources.model_copy(update={"revision": 0, "events": (receipt,)}),
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    runtime = build_runtime(play)
    for route in ("campaign", "stream"):
        owner = await runtime.project(route, cid, principal_id="alice")
        assert owner["wither_spell_findings"]
        assert "check" not in json.dumps(owner["wither_spell_findings"])
        for principal in ("bob", "watcher"):
            other = await runtime.project(route, cid, principal_id=principal)
            assert "wither_spell_findings" not in other
            assert "wither-spell:" not in json.dumps(other)
