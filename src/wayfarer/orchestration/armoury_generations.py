"""Capture operating-tool eligibility for private Armoury planning commands."""

from wayfarer import validation
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import (
    ARMOURY_OPERATING_KEY,
    ARMOURY_OPERATING_ORIGINAL,
    armoury_intent,
    canonical,
    intent_input,
    object_input,
    validate_armoury_keys,
)
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = ARMOURY_OPERATING_KEY
ORIGINAL_INPUT = ARMOURY_OPERATING_ORIGINAL
ACTIVE = True


def operating_policy(record: CommandInput, command_id: str, principal_id: str) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded Armoury input does not match its digest")
    validate_armoury_keys(record.text)
    raw = intent_input(record.text)
    armoury_intent(raw)
    payload = validation.mapping(validation.decode(raw))
    if KEY not in payload:
        return False
    command = payload.get("command")
    if (
        type(payload[KEY]) is not int
        or payload[KEY] != 1
        or payload.get("operation") != "armoury"
        or type(payload.get("generation")) is not int
        or payload.get("generation") != 1
        or type(payload.get("principal_id")) is not str
        or not isinstance(command, dict)
        or command.get("id") != command_id
        or set(payload)
        != {"operation", "generation", "principal_id", "command", KEY, ORIGINAL_INPUT}
    ):
        raise ValidationError("Unsupported recorded Armoury operating-tool generation")
    if payload["principal_id"] != principal_id:
        raise AuthorizationError("Recorded Armoury command belongs to another principal")
    return True


def stamp(text: str) -> str:
    payload = object_input(text)
    if payload is None or {KEY, ORIGINAL_INPUT}.intersection(payload):
        raise ValidationError("Armoury policy requires its original command envelope")
    payload[KEY] = 1
    payload[ORIGINAL_INPUT] = text
    result = canonical(payload)
    armoury_intent(result)
    return result


async def capture(store: Store, cid: str, command_id: str, principal_id: str) -> bool:
    replay = recorded_command.get()
    record = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command_id)
    )
    return ACTIVE if record is None else operating_policy(record, command_id, principal_id)
