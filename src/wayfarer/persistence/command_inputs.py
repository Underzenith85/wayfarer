"""Private command metadata with digest-checked, byte-exact intent identity."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "symptom_attribute_generation"
WRAPPED_INPUT = "symptom_command_input"
ORIGINAL_INPUT = "symptom_original_input"


def object_input(text: str) -> dict[str, object] | None:
    try:
        decoded = validation.decode(text)
    except json.JSONDecodeError:
        return None
    return validation.mapping(decoded) if isinstance(decoded, dict) else None


def canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def metadata(payload: dict[str, object] | None) -> bool:
    if payload is None or KEY not in payload:
        return False
    generation = payload[KEY]
    if type(generation) is not int or generation != 1:
        raise ValidationError("Unsupported recorded Symptoms attribute generation")
    if WRAPPED_INPUT in payload and (
        set(payload) != {KEY, WRAPPED_INPUT} or not isinstance(payload[WRAPPED_INPUT], str)
    ):
        raise ValidationError("Invalid recorded Symptoms input wrapper")
    if ORIGINAL_INPUT in payload:
        original = payload[ORIGINAL_INPUT]
        decoded = object_input(original) if isinstance(original, str) else None
        intended = {
            key: value for key, value in payload.items() if key not in {KEY, ORIGINAL_INPUT}
        }
        if decoded is None or canonical(decoded) != canonical(intended):
            raise ValidationError("Invalid recorded Symptoms original input")
    return True


def generation(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    return metadata(object_input(record.text))


def stamp(text: str, *, retain_original: bool = True) -> str:
    payload = object_input(text)
    if metadata(payload):
        return text
    if payload is None:
        payload = {WRAPPED_INPUT: text}
    elif retain_original:
        payload[ORIGINAL_INPUT] = text
    payload[KEY] = 1
    return canonical(payload)


def original_input(text: str) -> str:
    payload = object_input(text)
    if not metadata(payload):
        return text
    assert payload is not None
    if WRAPPED_INPUT in payload:
        return validation.string(payload[WRAPPED_INPUT])
    if ORIGINAL_INPUT in payload:
        return validation.string(payload[ORIGINAL_INPUT])
    # The first metadata-bearing version kept only this canonical object. Keep
    # its exact encoded identity; do not accept arbitrary whitespace changes.
    return canonical({key: value for key, value in payload.items() if key != KEY})


def same_input(record: CommandInput, requested: str) -> bool:
    """Verify stored bytes before removing only validated private metadata."""
    recorded_generation = generation(record)
    metadata(object_input(requested))
    if payload_digest({"input": requested}) == record.payload_hash:
        return True
    if not recorded_generation or record.text is None:
        return False
    return original_input(record.text) == original_input(requested)


def replay_payload(text: str) -> object:
    raw = original_input(text)
    try:
        return validation.decode(raw)
    except json.JSONDecodeError:
        return raw
