"""Retain exact public recovery intent behind one strict private generation."""

import json

from wayfarer import validation
from wayfarer.engine.rules.types.care_environment import captured
from wayfarer.errors import ValidationError

KEY = "medical_minimum_generation"
ORIGINAL = "medical_minimum_original_input"
ENVIRONMENT = "medical_care_environment"
KINDS = frozenset({"first-aid", "physician", "resuscitate", "stabilize", "repair-lasting"})


def _object(text: object) -> dict[str, object] | None:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return validation.mapping(value) if isinstance(value, dict) else None


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in {KEY, ORIGINAL, ENVIRONMENT} and key in result:
            raise ValidationError("Duplicate medical minimum generation metadata")
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
    raw = payload.get(ORIGINAL)
    source = _object(raw)
    command = payload.get("command")
    intended = {
        name: value for name, value in payload.items() if name not in {KEY, ORIGINAL, ENVIRONMENT}
    }
    if (
        type(payload.get(KEY)) is not int
        or payload.get(KEY) != 1
        or set(payload) != {"operation", "command", KEY, ORIGINAL, ENVIRONMENT}
        or not isinstance(payload.get(ENVIRONMENT), dict)
        or payload.get("operation") != "gurps-recovery"
        or not isinstance(command, dict)
        or command.get("kind") not in KINDS
        or source is None
        or {KEY, ORIGINAL, ENVIRONMENT}.intersection(source)
        or _canonical(source) != _canonical(intended)
        or text != _canonical(payload)
    ):
        raise ValidationError("Invalid medical minimum generation")
    captured(payload[ENVIRONMENT])
    return validation.string(raw)


def stamp(text: str, environment: dict[str, object]) -> str:
    payload = _object(text)
    if payload is None or {KEY, ORIGINAL, ENVIRONMENT}.intersection(payload):
        raise ValidationError("Medical minimum policy requires its original input")
    payload[KEY], payload[ORIGINAL] = 1, text
    payload[ENVIRONMENT] = environment
    result = _canonical(payload)
    intent(result)
    return result
