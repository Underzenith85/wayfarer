"""Actual MedicalService trusted context, retry and historical-input boundaries."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, open_store, played, seed_campaign
from test_medical_service import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.medical_context import (
    KEY,
    ORIGINAL,
    MedicalCapture,
    admitted_intent,
    canonical,
    digest,
    recorded,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.persistence.command_inputs import stamp
from wayfarer.persistence.events import CommandInput, payload_digest


async def fixture(path: Path, backend: str) -> tuple[str, PlayService]:
    cid, source, _ = await setup(path)
    initial = await source.store.read(cid)
    store = open_store(path / "focused", backend=backend)
    await seed_campaign(store, initial)
    return cid, build_play(path / "play", source.engine, store=store, rng=RecordedDice(()))


def begin() -> BeginRecovery:
    return BeginRecovery(
        id="medical", actor_id="a", expected_revision=0, kind="rest", target_id="a", seconds=1200
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_provisional_then_locked_snapshot_and_exact_restart_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    calls: list[int] = []

    def resolver(_play: PlayService, state: PlayState, target: str) -> CareEnvironment:
        assert target == "a"
        calls.append(state.revision)
        return CareEnvironment(technology_level=4, food=True, water=True, surgical_modifier=-2)

    service = MedicalService(play, resolver)
    result = await service.execute(cid, begin(), principal_id="a")
    assert result.status == "pending" and calls == [0, 0]
    state = play._load(await play.store.read(cid))
    assert state.resources.recovery_tasks[0].technology_level == 4
    saved = await play.store.command_input(cid, "medical")
    assert saved is not None
    original, captured = recorded(saved)
    assert captured is not None and captured.environment.surgical_modifier == -2
    assert captured.source_kind == "trusted-scenario-snapshot"
    assert original == json.dumps(
        {"operation": "gurps-recovery", "command": begin().model_dump(mode="json")}, sort_keys=True
    )
    history = await play.store.history(cid)
    before = await play.store.read(cid)

    def refuse(*args: object) -> CareEnvironment:
        raise AssertionError("retry must not refresh trusted care")

    restarted = MedicalService(
        build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())),
        refuse,
    )
    assert await restarted.execute(cid, begin(), principal_id="a") == result
    with pytest.raises(ConflictError, match="different input"):
        await restarted.execute(cid, begin().model_copy(update={"seconds": 1800}), principal_id="a")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert await play.store.command_input(cid, "medical") == saved
    assert await play.store.replay(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_changed_live_care_refuses_inside_cas_without_rng_or_mutation(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend)
    calls: list[int] = []

    def never_reduce(*args: object, **kwargs: object) -> None:
        raise AssertionError("Changed source must refuse before treatment reduction or RNG")

    monkeypatch.setattr("wayfarer.orchestration.medical.apply_recovery", never_reduce)

    def changing(_play: PlayService, state: PlayState, _target: str) -> CareEnvironment:
        calls.append(state.revision)
        return CareEnvironment(technology_level=4 if len(calls) == 1 else 5)

    before = await play.store.read(cid)
    history = await play.store.history(cid)
    with pytest.raises(ConflictError, match="environment changed"):
        await MedicalService(play, changing).execute(cid, begin(), principal_id="a")
    assert calls == [0, 0]
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authority_and_stale_refuse_before_trusted_callback(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend)

    def never_reduce(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unauthorized or stale source must refuse before treatment")

    monkeypatch.setattr("wayfarer.orchestration.medical.apply_recovery", never_reduce)

    def refuse(*args: object) -> CareEnvironment:
        raise AssertionError("unauthorized/stale must not inspect care")

    service = MedicalService(play, refuse)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    with pytest.raises(ValidationError, match="authenticated"):
        await service.execute(cid, begin(), principal_id="b")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, begin().model_copy(update={"expected_revision": 1}), principal_id="a"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("bad", [None, True, False, 1.0, "1", 2])
async def test_rehashed_invalid_or_missing_version_is_not_care(
    tmp_path: Path, bad: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, "sqlite")
    await MedicalService(play, lambda *_: CareEnvironment()).execute(cid, begin(), principal_id="a")
    saved = await play.store.command_input(cid, "medical")
    assert saved is not None
    original, captured = recorded(saved)
    assert captured is not None
    value = captured.model_dump(mode="json")
    if bad is None:
        del value["generation"]
    else:
        value["generation"] = bad
    value["snapshot_digest"] = digest(
        {key: entry for key, entry in value.items() if key != "snapshot_digest"}
    )
    raw = json.loads(original) | {KEY: value, ORIGINAL: original}
    with pytest.raises(SchemaError):
        admitted_intent(canonical(raw))
    row = (await played(play.store, cid))[-1]
    text = stamp(canonical(raw))
    malformed = replace(row, command_input=text, payload_hash=payload_digest({"input": text}))

    def never_care(*args: object) -> CareEnvironment:
        raise AssertionError("Malformed private context must not consult care")

    def never_reduce(*args: object, **kwargs: object) -> None:
        raise AssertionError("Malformed private context must not reach treatment or RNG")

    monkeypatch.setattr("wayfarer.orchestration.medical.apply_recovery", never_reduce)
    with replay_inputs(malformed), pytest.raises(SchemaError):
        await MedicalService(play, never_care).execute(cid, begin(), principal_id="a")


async def test_historical_begin_missing_context_explicitly_unavailable(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path, "sqlite")
    await MedicalService(play, lambda *_: CareEnvironment()).execute(cid, begin(), principal_id="a")
    row = (await played(play.store, cid))[-1]
    original, _ = recorded(CommandInput(row.payload_hash, row.command_input))
    old = stamp(original)
    historic = replace(row, command_input=old, payload_hash=payload_digest({"input": old}))

    def refuse(*args: object) -> CareEnvironment:
        raise AssertionError("historical begin may not infer care")

    with (
        replay_inputs(historic),
        pytest.raises(ValidationError, match="original medical care context unavailable"),
    ):
        await MedicalService(play, refuse).execute(cid, begin(), principal_id="a")


async def test_source_kind_is_required_strict_and_configuration_default_is_explicit(
    tmp_path: Path,
) -> None:
    from wayfarer.orchestration.player_medical import default_environment

    cid, play = await fixture(tmp_path, "sqlite")
    await MedicalService(play, default_environment, source_kind="configuration-default").execute(
        cid, begin(), principal_id="a"
    )
    saved = await play.store.command_input(cid, "medical")
    assert saved is not None
    _, captured = recorded(saved)
    assert captured is not None and captured.source_kind == "configuration-default"
    assert captured.environment.technology_level == (
        play.engine.reviewer.compiler.policy.technology_level or 0
    )
    for value in (None, True, "custom-callback"):
        raw = captured.model_dump(mode="json")
        if value is None:
            del raw["source_kind"]
        else:
            raw["source_kind"] = value
        with pytest.raises(SchemaError):
            MedicalCapture.model_validate(raw)


async def test_legacy_variant_raw_finish_uses_real_pretask_without_care_inference(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.actions import Wait
    from wayfarer.engine.simulation.health.medical.commands import FinishRecovery
    from wayfarer.orchestration.replay import execute_recorded

    cid, play = await fixture(tmp_path, "sqlite")
    play = build_play(tmp_path / "seeded", play.engine, store=play.store)
    service = MedicalService(play, lambda *_: CareEnvironment())
    await service.execute(cid, begin(), principal_id="a")
    await play.execute(
        cid, Wait(id="elapsed", actor_id="a", expected_revision=1, ticks=1200), principal_id="a"
    )
    before = await play.store.read(cid)
    finish = FinishRecovery(id="finish", actor_id="a", expected_revision=2, task_id="medical")
    await service.execute(cid, finish, principal_id="a")
    row = (await played(play.store, cid))[-1]
    # Compatibility diagnostic: real accepted task/outcome, old alias and exact raw encoding.
    command = finish.model_dump(mode="json") | {"kind": "finish-recovery-variant"}
    raw = json.dumps({"operation": "gurps-recovery", "command": command}, indent=2) + "\n"
    old = stamp(raw)
    historic = replace(row, command_input=old, payload_hash=payload_digest({"input": old}))
    store = open_store(tmp_path / "historical", backend="sqlite")
    await seed_campaign(store, before)
    replay = build_play(tmp_path / "replay", play.engine, store=store)
    await execute_recorded(replay, historic)
    saved = await store.command_input(cid, "finish")
    assert saved == CommandInput(historic.payload_hash, old)
    assert await store.read(cid) == row.state_after

    def refuse(*args: object) -> CareEnvironment:
        raise AssertionError("legacy finish retry may not infer care")

    history = await store.history(cid)
    await MedicalService(replay, refuse).execute(cid, finish, principal_id="a")
    assert await store.history(cid) == history and await store.command_input(cid, "finish") == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_competing_medical_cas_keeps_only_one_captured_task(
    tmp_path: Path, backend: str
) -> None:
    import asyncio

    cid, play = await fixture(tmp_path, backend)
    service = MedicalService(play, lambda *_: CareEnvironment())
    results = await asyncio.gather(
        service.execute(cid, begin(), principal_id="a"),
        service.execute(cid, begin().model_copy(update={"id": "competing"}), principal_id="a"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    state = play._load(await play.store.read(cid))
    assert state.revision == 1 and len(state.resources.recovery_tasks) == 1
    assert len(await played(play.store, cid)) == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_task_and_private_capture_rollback_after_candidate_checkpoint(
    tmp_path: Path, backend: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    cid, play = await fixture(tmp_path, backend)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await MedicalService(failing, lambda *_: CareEnvironment()).execute(
            cid, begin(), principal_id="a"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert await play.store.command_input(cid, "medical") is None
