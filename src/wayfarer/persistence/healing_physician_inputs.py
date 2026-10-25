"""Strict B173 policy metadata retaining the spell producer's exact intent."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError

KEY = "healing_physician_generation"
ORIGINAL = "healing_physician_original_input"
_BASE = frozenset(
    {
        "operation",
        "principal_id",
        "command",
        "targeting_generation",
        "check_generation",
        "item_sight_generation",
        "area_targeting_generation",
        "missile_attack_generation",
    }
)


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
        if key in {KEY, ORIGINAL} and key in result:
            raise ValidationError("Duplicate healing Physician generation metadata")
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
    source = _object(payload.get(ORIGINAL))
    command = payload.get("command")
    intended = {name: value for name, value in payload.items() if name not in {KEY, ORIGINAL}}
    if (
        type(payload.get(KEY)) is not int
        or payload.get(KEY) != 1
        or payload.get("operation") != "spell-lifecycle"
        or not {"operation", "principal_id", "command"} <= set(intended) <= _BASE
        or not isinstance(payload.get("principal_id"), str)
        or not isinstance(command, dict)
        or command.get("kind") != "start"
        or command.get("spell_id") not in {"minor-healing", "major-healing"}
        or source is None
        or {KEY, ORIGINAL}.intersection(source)
        or _canonical(source) != _canonical(intended)
        or text != _canonical(payload)
    ):
        raise ValidationError("Invalid healing Physician generation")
    return validation.string(payload[ORIGINAL])


def stamp(text: str) -> str:
    payload = _object(text)
    if payload is None or {KEY, ORIGINAL}.intersection(payload):
        raise ValidationError("Healing Physician policy requires its original input")
    payload[KEY], payload[ORIGINAL] = 1, text
    result = _canonical(payload)
    intent(result)
    return result
