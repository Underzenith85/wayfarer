"""Strict retained input/proof seams; these diagnostics do not author clinical facts."""

import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.engine.simulation.health.medical import minimum
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import CareEnvironment
from wayfarer.orchestration.medical_minimum_generations import read
from wayfarer.persistence.command_inputs import same_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.medical_minimum_inputs import ENVIRONMENT, KEY, ORIGINAL, intent
from wayfarer.persistence.medical_minimum_inputs import stamp as stamp_minimum


def public() -> str:
    return json.dumps(
        {
            "operation": "gurps-recovery",
            "command": {
                "id": "aid",
                "actor_id": "a",
                "kind": "first-aid",
                "target_id": "b",
            },
        },
        sort_keys=True,
    )


def recorded(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


def marked() -> tuple[ResourceState, RecoveryTask]:
    task = RecoveryTask(
        id="aid",
        actor_id="a",
        target_id="b",
        profile_id="gurps-basic-set-4e-2004",
        kind="first-aid",
        start=0,
        due=600,
        skill=3,
    )
    state = ResourceState(recovery_tasks=(task,))
    return minimum.record(state, task, enabled=True), task


def test_original_public_bytes_remain_exact_behind_strict_medical_metadata() -> None:
    raw = public()
    text = stamp_minimum(raw, asdict(CareEnvironment()))
    assert intent(text) == raw and same_input(recorded(text), raw)
    assert not same_input(recorded(text), raw + " ")
    assert not same_input(recorded(text), raw.replace('"b"', '"different-patient"'))
    assert read(recorded(text), eligible=True, command_id="aid", actor_id="a").enabled
    assert not read(recorded(raw), eligible=True, command_id="aid", actor_id="a").enabled
    assert same_input(recorded(raw), raw) and not same_input(recorded(raw), raw + " ")
    for identifier, actor in (("different-command", "a"), ("aid", "different-actor")):
        with pytest.raises(ValidationError, match="producer"):
            read(recorded(text), eligible=True, command_id=identifier, actor_id=actor)


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 0, 2])
def test_medical_input_generation_refuses_coercion_and_unknown_version(version: object) -> None:
    payload = json.loads(stamp_minimum(public(), asdict(CareEnvironment())))
    payload[KEY] = version
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError, match="generation"):
        read(recorded(text), eligible=True, command_id="aid", actor_id="a")


@pytest.mark.parametrize("key", [KEY, ORIGINAL, ENVIRONMENT])
@pytest.mark.parametrize("outer", [False, True])
def test_private_duplicate_keys_are_refused_before_outer_unwrapping(key: str, outer: bool) -> None:
    valid = stamp_minimum(public(), asdict(CareEnvironment()))
    if outer:
        valid = stamp(valid, version=3)
    payload = json.loads(valid)
    fragment = json.dumps(key) + ":" + json.dumps(payload[key], separators=(",", ":"))
    duplicated = valid.replace(fragment, fragment + "," + fragment)
    assert duplicated != valid
    with pytest.raises(ValidationError, match="Duplicate medical"):
        read(recorded(duplicated), eligible=True, command_id="aid", actor_id="a")
    with pytest.raises(ValidationError, match="Duplicate medical"):
        same_input(recorded(duplicated), public())


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("generation", 1.0),
        ("start", True),
        ("start", 0.0),
        ("snapshot_digest", "invalid"),
        ("actor_id", "other"),
    ],
)
def test_private_producer_numeric_and_task_bindings_are_strict(field: str, value: object) -> None:
    state, task = marked()
    event = state.events[0]
    payload = json.loads(event.kind)
    payload[field] = value
    corrupt = state.model_copy(
        update={"events": (event.model_copy(update={"kind": json.dumps(payload)}),)}
    )
    with pytest.raises(ValidationError, match="(?i)medical minimum"):
        minimum.enabled(corrupt, task)


def test_duplicate_proof_and_changed_captured_score_refuse() -> None:
    state, task = marked()
    with pytest.raises(ValidationError, match="Duplicate"):
        minimum.enabled(state.model_copy(update={"events": state.events + state.events}), task)
    with pytest.raises(ValidationError, match="captured task"):
        minimum.enabled(state, task.model_copy(update={"skill": 4}))


def test_only_authenticated_settled_voluntary_task_can_interrupt_at_due_boundary() -> None:
    state, task = marked()
    interrupted = task.model_copy(
        update={"status": "interrupted", "interrupted_at": 600, "settled": True}
    )
    accepted = state.model_copy(update={"game_time": 600, "recovery_tasks": (interrupted,)})
    ResourceState.model_validate(accepted)
    with pytest.raises(SchemaError, match="interruption time"):
        ResourceState.model_validate(accepted.model_copy(update={"events": ()}))
    with pytest.raises(SchemaError, match="interruption time"):
        ResourceState.model_validate(
            accepted.model_copy(
                update={"recovery_tasks": (interrupted.model_copy(update={"settled": False}),)}
            )
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("technology_level", True),
        ("food", "false"),
        ("physician_id", 3),
        ("surgical_modifier", 1.0),
    ],
)
def test_input_identity_refuses_malformed_captured_environment(field: str, value: object) -> None:
    payload = json.loads(stamp_minimum(public(), asdict(CareEnvironment())))
    payload[ENVIRONMENT][field] = value
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError, match="environment"):
        same_input(recorded(text), public())
