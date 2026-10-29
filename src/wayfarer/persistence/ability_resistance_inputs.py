"""Exact original ability intent beneath independent current resistance policy."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError

KEY = "ability_resistance_attempt_generation"
ORIGINAL = "ability_resistance_original_input"
_OTHER = frozenset({"ability_rule16_generation", "ability_offshift_generation"})


def _object(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return validation.mapping(value) if isinstance(value, dict) else None


def _canonical(raw: object) -> str:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in {KEY, ORIGINAL} and key in result:
            raise ValidationError("Duplicate ability resistance policy metadata")
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
    if payload is None or not {KEY, ORIGINAL}.intersection(payload):
        return text
    raw = payload.get(ORIGINAL)
    principal = payload.get("principal_id")
    command = payload.get("command")
    if (
        type(payload.get(KEY)) is not int
        or payload.get(KEY) != 1
        or payload.get("operation") != "ability"
        or not isinstance(raw, str)
        or not isinstance(principal, str)
        or not principal
        or not isinstance(command, dict)
        or set(payload) - {"operation", "principal_id", "command", KEY, ORIGINAL} - _OTHER
        or any(type(payload[k]) is not int or payload[k] != 1 for k in _OTHER & payload.keys())
        or text != _canonical(payload)
    ):
        raise ValidationError("Invalid ability resistance policy")
    validate_keys(raw)
    source = _object(raw)
    if source is None:
        prefix = principal + ":"
        supplied = _object(raw[len(prefix) :]) if raw.startswith(prefix) else None
        source = {"operation": "ability", "principal_id": principal, "command": supplied}
    expected = {k: v for k, v in payload.items() if k not in {KEY, ORIGINAL}}
    if KEY in source or ORIGINAL in source or source != expected:
        raise ValidationError("Ability resistance original identity changed")
    return raw


def stamp(base: str, principal: str, command: dict[str, object]) -> str:
    source = _object(base)
    envelope = source or {"operation": "ability", "principal_id": principal, "command": command}
    text = _canonical({**envelope, KEY: 1, ORIGINAL: base})
    intent(text)
    return text
