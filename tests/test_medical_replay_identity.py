"""Accepted opaque care retries retain identity without consulting changed care."""

from pathlib import Path

import pytest
from support.medical_replay import begin, fixture, runtime
from support.runtime import build_play

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.medical import CareEnvironment


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_opaque_medical_retry_retains_original_identity_without_resolver(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, care = await fixture(tmp_path, backend)
    command = await begin(play, cid, care)
    saved = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    care.environment = CareEnvironment(technology_level=2, food=False, water=False)
    calls = care.calls
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    access = runtime(restarted, care)
    await access.submit_json(cid, command, principal_id="bob")
    assert care.calls == calls
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream
    for changed in (
        {**command, "choice_id": "unrecognized-care-choice"},
        {**command, "actor_id": "a"},
        {**command, "expected_revision": int(str(command["expected_revision"])) + 1},
    ):
        with pytest.raises((AuthorizationError, ConflictError, ValidationError)):
            await access.submit_json(cid, changed, principal_id="bob")
        assert care.calls == calls
        assert await play.store.read(cid) == saved
        assert await play.store.history(cid) == history
        assert await play.store.stream(cid) == stream
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_opaque_medical_retry_cannot_reuse_other_family_receipt(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, care = await fixture(tmp_path, backend)
    # The real ChooseDefense receipt already owns this ID; care may not silently reuse it.
    with pytest.raises((ConflictError, ValidationError)):
        await begin(play, cid, care, identity="response")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_care_capture_is_private_in_registered_campaign_and_stream(
    tmp_path: Path, backend: str
) -> None:
    import json

    cid, play, _, care = await fixture(tmp_path, backend)
    await begin(play, cid, care)
    captured = await play.store.command_input(cid, "bandage")
    assert captured is not None and captured.text is not None
    assert "medical_context" in captured.text and "trusted-scenario-snapshot" in captured.text
    access = runtime(play, care)
    for principal in ("alice", "bob", "watcher"):
        for projection in (
            await access.read(cid, principal_id=principal),
            await access.project("stream", cid, principal_id=principal),
        ):
            public = json.dumps(projection)
            for secret in (
                "medical_context",
                "medical_original_input",
                "snapshot_digest",
                "prestate_digest",
                "trusted-scenario-snapshot",
            ):
                assert secret not in public
