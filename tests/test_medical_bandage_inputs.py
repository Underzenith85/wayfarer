"""The independent unrolled producer preserves intent and rejects false context."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from wayfarer.adventures.roadwatch import engine
from wayfarer.engine.rules.types.care_environment import CareEnvironment
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import MedicalService
from wayfarer.orchestration.medical_bandage_generations import read
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.command_inputs import same_input
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.medical_bandage_inputs import ENVIRONMENT, KEY, ORIGINAL, intent, stamp


def begin() -> BeginRecovery:
    return BeginRecovery(
        id="bandage-1",
        actor_id="b",
        expected_revision=8,
        kind="bandage",
        target_id="a",
        wound_id="injury:actual",
    )


def original(command: BeginRecovery | FinishRecovery) -> str:
    return json.dumps(
        {"operation": "gurps-recovery", "command": command.model_dump(mode="json")}, sort_keys=True
    )


def saved(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


def encode(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def test_exact_unmarked_plain_producer_bytes_and_policy_remain_unchanged(tmp_path: Path) -> None:
    play = PlayService(AsyncSQLiteStore(tmp_path / "unopened.sqlite3"), engine())

    def plain(_play: PlayService, _state: PlayState, _target: str) -> CareEnvironment:
        return CareEnvironment(technology_level=3)

    command = begin()
    plan = MedicalService(play, plain).plan(
        play, "gurps-basic-set-4e-2004", command, principal_id="b"
    )
    assert plan.payload == original(command)
    assert plan.assess is None
    assert intent(plan.payload) == plan.payload
    old = read(saved(plan.payload), eligible=True, command_id=command.id, actor_id=command.actor_id)
    assert not old.enabled and old.saved and old.environment is None


def test_begin_captures_context_and_finish_has_no_new_environment() -> None:
    command = begin()
    raw = original(command)
    environment = asdict(CareEnvironment(technology_level=3))
    captured = stamp(raw, environment)
    assert intent(captured) == raw
    assert same_input(saved(captured), raw)
    assert not same_input(saved(captured), original(command.model_copy(update={"target_id": "b"})))
    result = read(saved(captured), eligible=True, command_id=command.id, actor_id="b")
    assert result.enabled and result.saved and result.environment == environment
    finish = FinishRecovery(id="finish-1", actor_id="b", expected_revision=10, task_id=command.id)
    finished = stamp(original(finish), None)
    assert ENVIRONMENT not in json.loads(finished)
    assert intent(finished) == original(finish)
    result = read(saved(finished), eligible=True, command_id=finish.id, actor_id="b")
    assert result.enabled and result.saved and result.environment is None


@pytest.mark.parametrize("version", [None, True, False, 0, 2, -1, "1", 1.0])
def test_false_or_future_versions_do_not_silently_downgrade(version: object) -> None:
    payload = json.loads(stamp(original(begin()), asdict(CareEnvironment(technology_level=3))))
    payload[KEY] = version
    with pytest.raises(ValidationError):
        intent(encode(payload))


@pytest.mark.parametrize(
    "change",
    [
        "missing-environment",
        "orphan-environment",
        "forged-origin",
        "duplicate-version",
        "finish-new-environment",
    ],
)
def test_missing_or_forged_context_origin_rejected(change: str) -> None:
    text = stamp(original(begin()), asdict(CareEnvironment(technology_level=3)))
    payload = json.loads(text)
    if change == "missing-environment":
        del payload[ENVIRONMENT]
    elif change == "orphan-environment":
        del payload[KEY]
    elif change == "forged-origin":
        payload[ORIGINAL] = original(begin().model_copy(update={"actor_id": "a"}))
    elif change == "duplicate-version":
        text = text.replace('"' + KEY + '":1', '"' + KEY + '":1,"' + KEY + '":1')
    else:
        finish = FinishRecovery(
            id="finish-1", actor_id="b", expected_revision=10, task_id="bandage-1"
        )
        payload = json.loads(stamp(original(finish), None))
        payload[ENVIRONMENT] = asdict(CareEnvironment(technology_level=3))
    with pytest.raises(ValidationError):
        intent(text if change == "duplicate-version" else encode(payload))


@pytest.mark.parametrize(
    "field,value", [("technology_level", True), ("equipment_quality_modifier", 0.0), ("unknown", 1)]
)
def test_environment_types_and_shape_are_strict(field: str, value: object) -> None:
    environment: dict[str, object] = asdict(CareEnvironment(technology_level=3))
    environment[field] = value
    with pytest.raises(ValidationError):
        stamp(original(begin()), environment)


def test_digest_producer_actor_and_profile_mismatch_rejected() -> None:
    text = stamp(original(begin()), asdict(CareEnvironment(technology_level=3)))
    for record, eligible, identifier, actor in (
        (CommandInput("0" * 64, text), True, "bandage-1", "b"),
        (saved(text), False, "bandage-1", "b"),
        (saved(text), True, "other", "b"),
        (saved(text), True, "bandage-1", "a"),
    ):
        with pytest.raises(ValidationError):
            read(record, eligible=eligible, command_id=identifier, actor_id=actor)
