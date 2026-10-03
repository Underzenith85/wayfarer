"""Private after-hit capture cannot enable unrelated Task combat semantics."""

import json

import pytest

from wayfarer.errors import ValidationError
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack
from wayfarer.orchestration.task_combat_generations import KEY, features, producer
from wayfarer.persistence.command_inputs import combat_intent, intent_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest


def record(selected: list[str] | None) -> CommandInput:
    command = BeginOpponentAttack(
        id="begin", actor_id="b", expected_revision=2, encounter_id="fight", attack_id="release"
    )
    payload: dict[str, object] = {
        "operation": "task-host",
        "principal_id": "bob",
        "command": command.model_dump(mode="json"),
    }
    if selected is not None:
        payload[KEY] = selected
    text = stamp(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return CommandInput(payload_digest({"input": text}), text)


def test_old_begin_retains_absent_capture_and_new_begin_retains_exact_caller_intent() -> None:
    old = record(None)
    new = record(["missile-interposition"])
    assert features(old) == frozenset()
    assert features(new) == frozenset({"missile-interposition"})
    assert isinstance(producer(new), BeginOpponentAttack)
    assert old.text is not None and new.text is not None
    assert combat_intent(intent_input(new.text)) == intent_input(old.text)


@pytest.mark.parametrize("selected", [["grenade-fuse"], ["secondary-object-blasts"], ["future"]])
def test_begin_cannot_claim_delivery_or_fragment_semantics(selected: list[str]) -> None:
    with pytest.raises(ValidationError, match="generation"):
        features(record(selected))


def test_changed_begin_capture_bytes_fail_before_replay() -> None:
    saved = record(["missile-interposition"])
    with pytest.raises(ValidationError, match="digest"):
        features(CommandInput("0" * 64, saved.text))
