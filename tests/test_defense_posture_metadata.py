"""Independent defense generations preserve exact bytes and reject forged metadata."""

import json

import pytest

from wayfarer.errors import ValidationError
from wayfarer.persistence.command_inputs import (
    DEFENSE_POSTURE_KEY,
    KEY,
    ORIGINAL_INPUT,
    WRAPPED_INPUT,
    defense_posture_generation,
    generation_version,
    original_input,
    replay_payload,
    same_input,
    stamp,
    stamp_defense_postures,
)
from wayfarer.persistence.events import CommandInput, payload_digest


def recorded(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    "text", [' { "operation" : "combat", "command": {"id":"a"} } ', "opaque:a", '[ "a", 2 ]']
)
def test_independent_posture_capture_preserves_existing_original_bytes_and_generation(
    version: int, text: str
) -> None:
    previous = stamp(text, version=version)
    assert not defense_posture_generation(recorded(previous))
    current = stamp_defense_postures(previous)
    payload = json.loads(current)
    assert payload[DEFENSE_POSTURE_KEY] == 1 and generation_version(recorded(current)) == version
    assert original_input(current) == original_input(previous) == text
    assert replay_payload(current) == replay_payload(previous)
    assert same_input(recorded(current), text)
    assert same_input(recorded(previous), current)
    assert defense_posture_generation(recorded(current))
    with pytest.raises(ValidationError, match="digest"):
        defense_posture_generation(CommandInput("0" * 64, current))
    changed = text.replace("a", "b")
    assert not same_input(recorded(current), changed)


@pytest.mark.parametrize("bad", [False, True, 0, 2, "1", None, []])
def test_unknown_defense_version_never_becomes_a_new_caller_intent(bad: object) -> None:
    payload = json.loads(stamp('{"operation":"combat"}'))
    payload[DEFENSE_POSTURE_KEY] = bad
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError, match="defense posture generation"):
        defense_posture_generation(recorded(raw))
    with pytest.raises(ValidationError, match="defense posture generation"):
        original_input(raw)


def test_private_posture_version_requires_validated_envelope_and_exact_original() -> None:
    with pytest.raises(ValidationError, match="requires command metadata"):
        defense_posture_generation(recorded(json.dumps({DEFENSE_POSTURE_KEY: 1})))
    payload = {
        KEY: 2,
        DEFENSE_POSTURE_KEY: 1,
        "operation": "combat",
        ORIGINAL_INPUT: '{"operation":"resource"}',
    }
    with pytest.raises(ValidationError, match="original input"):
        defense_posture_generation(recorded(json.dumps(payload)))
    wrapped = {KEY: 1, DEFENSE_POSTURE_KEY: 1, WRAPPED_INPUT: "opaque", "unknown": 1}
    with pytest.raises(ValidationError, match="input wrapper"):
        defense_posture_generation(recorded(json.dumps(wrapped)))
    assert not defense_posture_generation(recorded('{"operation":"combat"}'))


def test_first_canonical_symptom_encoding_retains_its_exact_historical_identity() -> None:
    old = '{"command":{"id":"old"},"symptom_attribute_generation":1}'
    new = stamp_defense_postures(old)
    assert original_input(old) == original_input(new) == '{"command":{"id":"old"}}'
    assert same_input(recorded(old), new)
    assert defense_posture_generation(recorded(new))
