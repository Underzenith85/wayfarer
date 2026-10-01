"""Generation metadata preserves each existing service's input identity and replay."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_abilities import command as ability_command
from test_abilities import spec
from test_ability_service import setup as ability_setup
from test_actions import engine as action_engine
from test_resources import campaign as resource_campaign
from test_resources import engine as resource_engine
from test_resources import seed
from test_wave10 import prepare as recovery_setup

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.resources import Consume
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.resources import ResourceService
from wayfarer.orchestration.symptom_generations import (
    KEY,
    WRAPPED_INPUT,
    capture,
    correct_symptom_attributes,
    replay_payload,
    symptom_generation,
)
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import command_text


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [True, False])
async def test_ability_opaque_inputs_retry_and_replay_without_losing_authority(
    tmp_path: Path, backend: str, legacy: bool
) -> None:
    cid, original = await ability_setup(tmp_path / "setup", spec(), magic=True)
    initial = await original.store.read(cid)
    source = build_play(tmp_path / "source", original.engine)
    await seed_campaign(source.store, initial)
    value = ability_command()
    service = AbilityService(source)
    if legacy:
        state = source._load(initial)
        plan = service.plan(source, state, member_for(state, "a"), value, principal_id="a")
        assert plan.payload == "a:" + value.model_dump_json()
        with symptom_generation(False):
            await commit_command(
                source,
                cid,
                value.id,
                value.expected_revision,
                plan.payload,
                plan.resolve,
                actor_id="a",
                rng=source.rng,
            )
    else:
        await service.execute(cid, value, principal_id="a")
    record = (await source.store.history(cid))[-1]
    raw = command_text(record)
    assert raw.startswith("a:") is legacy
    if not legacy:
        assert json.loads(raw) == {KEY: 1, WRAPPED_INPUT: "a:" + value.model_dump_json()}
    clone = build_play(tmp_path / "clone", original.engine, backend=backend)
    await seed_campaign(clone.store, initial)
    with replay_inputs(record):
        assert clone.rules_context.correct_symptom_attributes is not legacy
        result = await AbilityService(clone).execute(cid, value, principal_id="a")
    assert result.outcome == "concentrating"
    assert await clone.store.read(cid) == record.state_after == await clone.store.replay(cid)
    replayed = (await clone.store.history(cid))[-1]
    assert command_text(replayed) == raw and replayed.payload_hash == record.payload_hash
    clone.rng = RecordedDice([])
    assert await AbilityService(clone).execute(cid, value, principal_id="a") == result
    with pytest.raises(ConflictError, match="different input"):
        await AbilityService(clone).execute(
            cid, value.model_copy(update={"channel_id": "changed"}), principal_id="a"
        )
    with pytest.raises(AuthorizationError):
        await AbilityService(clone).execute(cid, value, principal_id="b")
    with pytest.raises(ValidationError, match="No replay handler"):
        await execute_recorded(clone, record)
    assert await clone.store.read(cid) == record.state_after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [True, False])
async def test_recovery_raw_command_replays_through_its_strict_adapter(
    tmp_path: Path, backend: str, legacy: bool
) -> None:
    cid, original = await recovery_setup(tmp_path / "setup")
    initial = await original.store.read(cid)
    source = build_play(tmp_path / "source", original.engine)
    await seed_campaign(source.store, initial)
    value = RecoveryCommand(
        kind="apply_setback",
        id="capture",
        actor_id="gm",
        expected_revision=0,
        rule_id="capture",
        target_actor_id="a",
    )
    service = RecoveryService(source)
    if legacy:
        plan = service.plan(value)
        with symptom_generation(False):
            await commit_command(
                source,
                cid,
                value.id,
                value.expected_revision,
                plan.payload,
                plan.resolve,
                actor_id="gm",
                rng=source.rng,
            )
    else:
        await service.execute(cid, value, principal_id="gm")
    record = (await source.store.history(cid))[-1]
    assert (KEY in json.loads(command_text(record))) is not legacy
    assert RecoveryCommand.model_validate(replay_payload(command_text(record))) == value
    clone = build_play(tmp_path / "clone", original.engine, backend=backend)
    await seed_campaign(clone.store, initial)
    await execute_recorded(clone, record)
    assert await clone.store.read(cid) == record.state_after == await clone.store.replay(cid)
    replayed = (await clone.store.history(cid))[-1]
    assert command_text(replayed) == command_text(record)
    assert replayed.payload_hash == record.payload_hash
    restarted = PlayService(clone.store, clone.engine, rng=RecordedDice([]))
    await RecoveryService(restarted).execute(cid, value, principal_id="gm")
    with pytest.raises(ValidationError, match="authorized"):
        await RecoveryService(restarted).execute(cid, value, principal_id="a")
    with pytest.raises(ConflictError, match="different input"):
        await RecoveryService(restarted).execute(
            cid, value.model_copy(update={"target_actor_id": "b"}), principal_id="gm"
        )
    assert await clone.store.read(cid) == record.state_after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_resource_raw_command_retains_explicit_service_replay(
    tmp_path: Path, backend: str
) -> None:
    engine = resource_engine()
    initial = resource_campaign(engine)
    cid = initial["id"]
    source = ResourceService(open_store(tmp_path / "source"), engine)
    await source.create(initial, seed())
    initial = await source.store.read(cid)
    value = Consume(id="consume", actor_id="a", expected_revision=0, item_id="arrows", quantity=3)
    expected = await source.execute(cid, value, principal_id="a")
    record = (await source.store.history(cid))[-1]
    assert json.loads(command_text(record))[KEY] == 1
    decoded = replay_payload(command_text(record))
    assert Consume.model_validate(decoded) == value
    clone = ResourceService(open_store(tmp_path / "clone", backend=backend), engine)
    await seed_campaign(clone.store, initial)
    with replay_inputs(record):
        assert correct_symptom_attributes()
        assert await clone.execute(cid, decoded, principal_id="a") == expected
    assert await clone.store.read(cid) == record.state_after == await clone.store.replay(cid)
    replayed = (await clone.store.history(cid))[-1]
    assert command_text(replayed) == command_text(record)
    assert replayed.payload_hash == record.payload_hash
    assert await clone.execute(cid, value, principal_id="a") == expected
    with pytest.raises(ConflictError, match="different input"):
        await clone.execute(cid, value.model_copy(update={"quantity": 4}), principal_id="a")
    with pytest.raises(ValidationError, match="authorized"):
        await clone.execute(cid, value, principal_id="b")
    with pytest.raises(ValidationError, match="No replay handler"):
        await execute_recorded(PlayService(clone.store, action_engine()), record)
    assert await clone.store.read(cid) == record.state_after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "text", ['principal:{"id":"command"}', '["command", "document"]', '{ "id" : "command" }']
)
@pytest.mark.parametrize("legacy", [True, False])
async def test_indexed_capture_preserves_opaque_array_and_object_identity(
    tmp_path: Path, backend: str, text: str, legacy: bool
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = resource_campaign(resource_engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    encoded, generation = (text, False) if legacy else await capture(store, cid, "command", text)
    assert generation is not legacy

    def resolve(campaign: Campaign) -> CommandReceipt:
        campaign["revision"] += 1
        return CommandReceipt(action="resource", outcome="format-fixture")

    await store.commit_turn(cid, "command", 0, encoded, resolve, actor_id="a")
    record = (await store.history(cid))[-1]
    assert command_text(record) == encoded
    assert record.payload_hash == payload_digest({"input": encoded})
    captured, generation = await capture(store, cid, "command", text)
    assert captured == encoded and generation is not legacy
    assert await store.duplicate(cid, "command", captured) == record.state_after
    changed, _ = await capture(store, cid, "command", text.replace("command", "different"))
    with pytest.raises(ConflictError, match="different input"):
        await store.duplicate(cid, "command", changed)
    with pytest.raises(ValidationError, match="digest"):
        command_text(replace(record, command_input="corrupted"))


@pytest.mark.parametrize("bad", [None, 0, 2, True, "1"])
def test_decoded_replay_rejects_explicit_bad_generation(bad: object) -> None:
    with pytest.raises(ValidationError, match="Unsupported recorded Symptoms"):
        replay_payload(json.dumps({KEY: bad, "id": "command"}))


@pytest.mark.parametrize(
    "payload",
    [
        {KEY: 1, WRAPPED_INPUT: []},
        {KEY: 1, WRAPPED_INPUT: "opaque", "extra": "unrecognized"},
    ],
)
def test_decoded_replay_rejects_malformed_wrappers(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="Invalid recorded Symptoms input wrapper"):
        replay_payload(json.dumps(payload))
