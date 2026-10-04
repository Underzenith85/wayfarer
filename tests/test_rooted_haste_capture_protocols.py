"""Actual legacy Rooted commands retain captured admission under the new policy."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.rooted_feet import fixture, observe, revision
from support.runtime import played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    ADAPTER,
    CastRootedFeet,
    active_effect,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration import rooted_feet_generations
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.rooted_feet import RootedFeetService
from wayfarer.persistence.command_inputs import replay_payload, stamp
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_generation_one_commands_replay_and_retry_under_current_two(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rooted_feet_generations, "CURRENT", 1)
    cid, play, initial = await fixture(tmp_path, backend)
    await observe(play, cid)
    command = CastRootedFeet(
        id="legacy-root",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="legacy-root",
        subject_id="subject",
    )
    play.rng = secrets
    play.seeds = lambda: f"{2:064x}"
    receipt = await RootedFeetService(play).execute(cid, command, principal_id="cora")
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert active_effect(state.resources, "b") is not None
    records = await played(play.store, cid)
    original = tuple((r.command_input, r.payload_hash) for r in records)
    roots = tuple(r for r in records if r.command_id in ("physical", "legacy-root"))
    assert len(roots) == 2
    for record in roots:
        assert record.command_input is not None
        payload = validation.mapping(replay_payload(record.command_input))
        assert payload["operation"] == "rooted-feet" and payload["generation"] == 1
    monkeypatch.setattr(rooted_feet_generations, "CURRENT", 2)
    play.rng = RecordedDice(())
    assert await RootedFeetService(play).execute(cid, command, principal_id="cora") == receipt
    assert play.rng.exhausted() and await play.store.read(cid) == saved
    assert (
        tuple((r.command_input, r.payload_hash) for r in await played(play.store, cid)) == original
    )
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(e.folded and e.reexecuted for e in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("fault", ["bool", "float", "unsupported", "operation", "digest"])
async def test_actual_record_capture_rejects_malformed_private_envelope_before_rng(
    tmp_path: Path, backend: str, fault: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await observe(play, cid)
    source = next(r for r in await played(play.store, cid) if r.command_id == "physical")
    assert source.command_input is not None
    payload = validation.mapping(replay_payload(source.command_input))
    command = ADAPTER.validate_python(payload["command"])
    if fault == "operation":
        payload["operation"] = "haste"
    elif fault != "digest":
        payload["generation"] = {"bool": True, "float": 2.0, "unsupported": 3}[fault]
    encoded = stamp(json.dumps(payload, sort_keys=True))
    corrupted = replace(
        source,
        command_input=encoded,
        payload_hash="0" * 64 if fault == "digest" else payload_digest({"input": encoded}),
    )
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with replay_inputs(corrupted), pytest.raises(ValidationError):
        await RootedFeetService(play).execute(cid, command, principal_id="gm")
    assert play.rng.exhausted() and await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fresh_generation_two_authority_and_stale_refuse_atomically(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await observe(play, cid)
    command = CastRootedFeet(
        id="fresh-root",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="fresh-root",
        subject_id="subject",
    )
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await RootedFeetService(play).execute(cid, command, principal_id="alice")
    with pytest.raises(ConflictError):
        await RootedFeetService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="cora"
        )
    assert play.rng.exhausted() and await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    record = next(r for r in await played(play.store, cid) if r.command_id == "physical")
    assert record.command_input is not None
    assert validation.mapping(replay_payload(record.command_input))["generation"] == 2
