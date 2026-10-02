"""Private command metadata with digest-checked, byte-exact intent identity."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "symptom_attribute_generation"
WRAPPED_INPUT = "symptom_command_input"
ORIGINAL_INPUT = "symptom_original_input"
REACTION_KEY = "reaction_semantics_generation"
REACTION_ORIGINAL = "reaction_original_input"
REACTION_WRAPPED = "reaction_command_input"


def object_input(text: str) -> dict[str, object] | None:
    try:
        decoded = validation.decode(text)
    except json.JSONDecodeError:
        return None
    return validation.mapping(decoded) if isinstance(decoded, dict) else None


def canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def reaction_metadata(payload: dict[str, object] | None) -> bool:
    if payload is None:
        return False
    if REACTION_KEY not in payload:
        if REACTION_ORIGINAL in payload or REACTION_WRAPPED in payload:
            raise ValidationError("Reaction generation metadata requires its version")
        return False
    if type(payload[REACTION_KEY]) is not int or payload[REACTION_KEY] != 1:
        raise ValidationError("Unsupported recorded reaction generation")
    if REACTION_WRAPPED in payload:
        if set(payload) != {REACTION_KEY, REACTION_WRAPPED} or not isinstance(
            payload[REACTION_WRAPPED], str
        ):
            raise ValidationError("Invalid recorded reaction input wrapper")
        if object_input(validation.string(payload[REACTION_WRAPPED])) is not None:
            raise ValidationError("Reaction wrapper cannot hide an object input")
    else:
        raw = payload.get(REACTION_ORIGINAL)
        source = object_input(raw) if isinstance(raw, str) else None
        intended = {
            key: value
            for key, value in payload.items()
            if key not in {REACTION_KEY, REACTION_ORIGINAL}
        }
        if (
            source is None
            or {REACTION_KEY, REACTION_ORIGINAL, REACTION_WRAPPED}.intersection(source)
            or canonical(source) != canonical(intended)
        ):
            raise ValidationError("Invalid recorded reaction original input")
    return True


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
    requested_input = great_haste_intent(combat_intent(intent_input(requested)))
    recorded_input = (
        great_haste_intent(combat_intent(intent_input(record.text)))
        if record.text is not None
        else None
    )
    if payload_digest({"input": requested}) == record.payload_hash:
        return True
    if record.text is None or not (
        recorded_generation or reaction_metadata(object_input(record.text))
    ):
        return False
    return recorded_input == requested_input


def combat_intent(text: str) -> str:
    """Remove validated features only from the host's canonical combat envelope."""
    payload = object_input(text)
    key = "combat_protocol_features"
    task_key = "task_combat_protocol_features"
    if payload is not None and task_key in payload:
        raw_task = payload[task_key]
        command = payload.get("command")
        response = command.get("response") if isinstance(command, dict) else None
        if (
            key in payload
            or payload.get("operation") != "task-host"
            or not isinstance(command, dict)
            or command.get("kind") != "choose-opponent-attack"
            or not isinstance(response, dict)
            or response.get("kind") != "choose_defense"
            or not isinstance(raw_task, list)
            or any(not isinstance(item, str) for item in raw_task)
            or len(set(raw_task)) != len(raw_task)
            or not set(raw_task) <= {"grenade-fuse"}
            or text != canonical(payload)
        ):
            raise ValidationError("Invalid recorded task combat feature generation")
        return canonical({name: value for name, value in payload.items() if name != task_key})
    if payload is None or key not in payload:
        return text
    raw = payload[key]
    if (
        payload.get("operation") not in {"combat", "combat-random-unarmed"}
        or not isinstance(raw, list)
        or any(not isinstance(item, str) for item in raw)
        or len(set(raw)) != len(raw)
        or not set(raw) <= {"grenade-fuse", "maneuver-budget", "acrobatic-trait-bonuses"}
        or text != canonical(payload)
    ):
        raise ValidationError("Invalid recorded combat feature generation")
    return canonical({name: value for name, value in payload.items() if name != key})


def intent_input(text: str) -> str:
    """Recover exact caller bytes through the validated, ordered private layers."""
    raw = original_input(text)
    payload = object_input(raw)
    if not reaction_metadata(payload):
        return raw
    assert payload is not None
    return validation.string(
        payload[REACTION_WRAPPED] if REACTION_WRAPPED in payload else payload[REACTION_ORIGINAL]
    )


def replay_payload(text: str) -> object:
    raw = intent_input(text)
    try:
        return validation.decode(raw)
    except json.JSONDecodeError:
        return raw


def great_haste_intent(text: str) -> str:
    """Recover exact private host bytes only through validated generation metadata."""
    payload = object_input(text)
    key, original = "great_haste_combat_generation", "great_haste_original_input"
    if payload is None or not {key, original}.intersection(payload):
        return text
    raw = payload.get(original)
    source = object_input(raw) if isinstance(raw, str) else None
    intended = {name: value for name, value in payload.items() if name not in {key, original}}
    if (
        payload.get("operation") != "great-haste"
        or type(payload.get(key)) is not int
        or payload[key] != 1
        or source is None
        or {key, original}.intersection(source)
        or canonical(source) != canonical(intended)
    ):
        raise ValidationError("Invalid recorded Great Haste combat generation")
    return validation.string(raw)
