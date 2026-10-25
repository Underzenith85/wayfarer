"""Private source-binding generation for B349 offered-resistance caps."""

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import canonical, object_input, replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "malediction_waiver_generation"


def stamp(text: str, enabled: bool) -> str:
    if not enabled:
        return text
    payload = object_input(text)
    if payload is None:
        raise ValidationError("Malediction waiver generation requires a typed source binding")
    payload[KEY] = 1
    stamped = canonical(payload)
    version(CommandInput(payload_digest({"input": stamped}), stamped))
    return stamped


def version(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded Malediction waiver input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    if KEY not in payload:
        return False
    if type(payload[KEY]) is not int or payload[KEY] != 1:
        raise ValidationError("Unsupported recorded Malediction waiver generation")
    command = validation.mapping(payload.get("command"))
    if payload.get("operation") != "composed-attack" or command.get("kind") != "bind":
        raise ValidationError("Malediction waiver generation requires a source binding")
    return True


async def capture(store: Store, cid: str, command_id: str) -> bool:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    return True if saved is None else version(saved)
