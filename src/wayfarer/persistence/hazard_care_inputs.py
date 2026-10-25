"""Byte-exact public care intent behind strict independent private policy."""

import json

from wayfarer import validation
from wayfarer.engine.rules.types.hazard_care import captured, validate_command
from wayfarer.errors import ValidationError

KEY = "hazard_care_skill_minimum_generation"
CONTEXT = "hazard_care_context"
ORIGINAL = "hazard_care_original_input"


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
        if key in {KEY, CONTEXT, ORIGINAL} and key in result:
            raise ValidationError("Duplicate hazard care policy metadata")
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
    if payload is None or not {KEY, CONTEXT, ORIGINAL}.intersection(payload):
        return text
    raw = payload.get(ORIGINAL)
    source = _object(raw)
    command = payload.get("command")
    if (
        type(payload.get(KEY)) is not int
        or payload.get(KEY) not in (1, 2)
        or set(payload) != {"operation", "command", KEY, CONTEXT, ORIGINAL}
        or payload.get("operation") != "hazard-care"
        or not isinstance(command, dict)
        or source is None
        or set(source) != {"hazard-care"}
        or _canonical(source) != _canonical({"hazard-care": command})
        or text != _canonical(payload)
    ):
        raise ValidationError("Invalid hazard care minimum policy")
    validate_command(command)
    context = captured(payload[CONTEXT])
    if payload[KEY] == 2 and context.kind != "diagnosis":
        raise ValidationError("Diagnosis minimum generation requires its exact context")
    return validation.string(raw)


def stamp(text: str, context: dict[str, object], *, version: int = 1) -> str:
    source = _object(text)
    if source is None or set(source) != {"hazard-care"}:
        raise ValidationError("Hazard care policy requires its exact public input")
    payload = {
        "operation": "hazard-care",
        "command": source["hazard-care"],
        KEY: version,
        CONTEXT: context,
        ORIGINAL: text,
    }
    result = _canonical(payload)
    intent(result)
    return result
