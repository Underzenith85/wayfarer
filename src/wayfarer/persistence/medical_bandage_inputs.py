"""Strict captured context for an independently opted-in, unrolled producer."""

import json

from wayfarer import validation
from wayfarer.engine.rules.types.care_environment import captured
from wayfarer.errors import ValidationError

KEY = "medical_bandage_generation"
ORIGINAL = "medical_bandage_original_input"
ENVIRONMENT = "bandage_care_environment"


def _object(text: object) -> dict[str, object] | None:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return validation.mapping(value) if isinstance(value, dict) else None


def _canonical(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in {KEY, ORIGINAL, ENVIRONMENT} and key in result:
            raise ValidationError("Duplicate bandage capture metadata")
        result[key] = value
    return result


def validate_keys(text: str) -> None:
    try:
        json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError:
        return


def intent(text: str) -> str:
    validate_keys(text)
    payload = _object(text)
    if payload is None or not {KEY, ORIGINAL, ENVIRONMENT}.intersection(payload):
        return text
    command = payload.get("command")
    source = _object(payload.get(ORIGINAL))
    kind = command.get("kind") if isinstance(command, dict) else None
    expected = {"operation", "command", KEY, ORIGINAL}
    if kind == "bandage":
        expected.add(ENVIRONMENT)
    intended = {
        name: value for name, value in payload.items() if name not in {KEY, ORIGINAL, ENVIRONMENT}
    }
    if (
        type(payload.get(KEY)) is not int
        or payload.get(KEY) != 1
        or kind not in {"bandage", "finish-recovery"}
        or set(payload) != expected
        or payload.get("operation") != "gurps-recovery"
        or source is None
        or {KEY, ORIGINAL, ENVIRONMENT}.intersection(source)
        or _canonical(source) != _canonical(intended)
        or text != _canonical(payload)
    ):
        raise ValidationError("Invalid captured bandage producer")
    if kind == "bandage":
        captured(payload[ENVIRONMENT])
    return validation.string(payload[ORIGINAL])


def stamp(text: str, environment: dict[str, object] | None) -> str:
    payload = _object(text)
    if payload is None or {KEY, ORIGINAL, ENVIRONMENT}.intersection(payload):
        raise ValidationError("Bandage capture requires its original command")
    payload[KEY], payload[ORIGINAL] = 1, text
    if environment is not None:
        payload[ENVIRONMENT] = environment
    result = _canonical(payload)
    intent(result)
    return result
