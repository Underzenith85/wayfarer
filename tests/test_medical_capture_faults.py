"""Rehashed care faults reach real admission against the original pre-command state."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, open_store, played, seed_campaign
from test_medical_context_capture import begin, fixture

from scripts.replay_fixtures import capture, engine_for, verify_fixture
from wayfarer import validation
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.medical_context import KEY, canonical, digest
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.persistence.command_inputs import intent_input, stamp
from wayfarer.persistence.events import payload_digest


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "fault",
    [
        "configuration_digest",
        "profile_id",
        "prestate_digest",
        "command_id",
        "actor_id",
        "target_id",
        "expected_revision",
        "environment-int",
        "environment-bool",
        "environment-extra",
        "capture-extra",
        "mixed-namespace",
        "operation",
        "command-kind",
        "snapshot-digest",
        "input-digest",
    ],
)
async def test_rehashed_capture_fault_refuses_before_care_on_genuine_prestate(
    tmp_path: Path, backend: str, fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, source = await fixture(tmp_path / "accepted", backend)
    original = await source.store.read(cid)
    assert source._load(original).revision == begin().expected_revision == 0
    source.rng = secrets
    source.seeds = lambda: f"{1:064x}"
    await MedicalService(source, lambda *_: CareEnvironment(technology_level=4)).execute(
        cid, begin(), principal_id="a"
    )
    row = (await played(source.store, cid))[-1]
    assert row.reexecutable and row.command_input is not None
    payload = validation.mapping(validation.decode(intent_input(row.command_input)))
    context = validation.mapping(payload[KEY])
    if fault in (
        "configuration_digest",
        "profile_id",
        "prestate_digest",
        "command_id",
        "actor_id",
        "target_id",
    ):
        context[fault] = "different-original-source"
    elif fault == "expected_revision":
        context[fault] = 1
    elif fault.startswith("environment-"):
        env = validation.mapping(context["environment"])
        env[
            {
                "environment-int": "technology_level",
                "environment-bool": "food",
                "environment-extra": "client_effective_skill",
            }[fault]
        ] = {
            "environment-int": 4.0,
            "environment-bool": 1,
            "environment-extra": 20,
        }[fault]
        context["environment"] = env
    elif fault == "capture-extra":
        context["future_result"] = "pending"
    elif fault == "mixed-namespace":
        payload["combat_protocol_features"] = []
    elif fault == "operation":
        payload["operation"] = "recovery"
    elif fault == "command-kind":
        command = validation.mapping(payload["command"])
        command["kind"] = "apply_setback"
        payload["command"] = command
    context["snapshot_digest"] = (
        "0" * 64
        if fault == "snapshot-digest"
        else digest({k: v for k, v in context.items() if k != "snapshot_digest"})
    )
    payload[KEY] = context
    text = stamp(canonical(payload))
    malformed = replace(
        row,
        command_input=text,
        payload_hash="0" * 64 if fault == "input-digest" else payload_digest({"input": text}),
    )
    # A genuinely fresh store is seeded from BEFORE Begin, never accepted state_after.
    store = open_store(tmp_path / "precommand", backend=backend)
    await seed_campaign(store, original)
    play = build_play(tmp_path / "replay", source.engine, store=store)
    play.rng = secrets
    before = await store.read(cid)
    history, stream = await store.history(cid), await store.stream(cid)

    def never_care(*_args: object) -> CareEnvironment:
        raise AssertionError("Malformed replay refreshed trusted care")

    def never_reduce(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Malformed replay reached treatment")

    def no_dice(_self: SeededRandom, _upper: int, /) -> int:
        raise AssertionError("Malformed replay consumed randomness")

    monkeypatch.setattr("wayfarer.orchestration.medical.apply_recovery", never_reduce)
    monkeypatch.setattr(SeededRandom, "randbelow", no_dice)
    with replay_inputs(malformed), pytest.raises((SchemaError, ValidationError, ConflictError)):
        await MedicalService(play, never_care).execute(cid, begin(), principal_id="a")
    assert await store.read(cid) == before
    assert await store.history(cid) == history and await store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_registered_caller_cannot_supply_private_care_capture(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from support.medical_replay import choice, runtime
    from support.medical_replay import fixture as actual_fixture
    from support.wither_limb import revision

    cid, play, _, care = await actual_fixture(tmp_path, backend)
    access = runtime(play, care)
    offered = await access.read(cid, principal_id="bob")
    command: dict[str, object] = {
        "id": "forged-care",
        "actor_id": "b",
        "expected_revision": await revision(play, cid),
        "kind": "gurps_recovery",
        "choice_id": choice(offered, "bandage"),
        KEY: {"generation": 1, "environment": {"technology_level": 8}},
    }
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    calls = care.calls

    def never_reduce(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Caller-authored care reached treatment")

    monkeypatch.setattr("wayfarer.orchestration.medical.apply_recovery", never_reduce)
    with pytest.raises((SchemaError, ValidationError)):
        await access.submit_json(cid, command, principal_id="bob")
    assert care.calls == calls and await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream


async def test_authored_recovery_original_genesis_fixture_still_reexecutes(tmp_path: Path) -> None:
    authored = await capture("recovery", tmp_path / "authored")
    assert any(c.action == "recovery" for c in authored.commands)
    assert all("gurps-recovery" not in c.command_input for c in authored.commands)
    checks = await verify_fixture(
        authored, await engine_for("recovery", tmp_path / "engine"), tmp_path / "reexecute"
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
