"""Private authoritative Rule16 target-scope generation for ability receipts."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer import validation
from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import canonical, replay_payload
from wayfarer.persistence.events import CommandInput, CommandRecord, payload_digest

KEY = "ability_rule16_generation"


def payload(principal: str, command: AbilityCommand, enabled: bool) -> str:
    if not enabled:
        return principal + ":" + command.model_dump_json()
    return canonical(
        {
            "operation": "ability",
            "principal_id": principal,
            "command": command.model_dump(mode="json"),
            KEY: 1,
        }
    )


def version(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded ability input does not match its digest")
    decoded = replay_payload(record.text)
    if not isinstance(decoded, dict) or KEY not in decoded:
        return False
    if type(decoded[KEY]) is not int or decoded[KEY] != 1 or decoded.get("operation") != "ability":
        raise ValidationError("Unsupported recorded ability Rule16 generation")
    AbilityCommand.model_validate_json(json.dumps(decoded.get("command")))
    return True


async def capture(store: Store, cid: str, command_id: str) -> bool:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    return True if saved is None else version(saved)


def legacy_payload(record: CommandRecord, decoded: object) -> object:
    """Identify only the historical committed ability family, never guess a kind."""
    if (
        not isinstance(decoded, str)
        or record.event.get("action") != "resource"
        or record.event.get("outcome") != "ability"
    ):
        return decoded
    principal, command = _legacy_parts(record, decoded)
    return {
        "operation": "ability",
        "principal_id": principal,
        "command": command.model_dump(mode="json"),
    }


def _legacy_parts(record: CommandRecord, text: str) -> tuple[str, AbilityCommand]:
    matches: list[tuple[str, AbilityCommand]] = []
    # The exact historical producer is principal + ':' + a typed JSON command.
    # Principals may contain colons; only a complete valid command bound to the
    # recorded actor/id/revision can establish the separator. Never infer actor
    # identity as the principal, nor treat an arbitrary resource family as ability.
    offset = text.find(":{")
    while offset >= 0:
        try:
            command = AbilityCommand.model_validate_json(text[offset + 1 :])
        except SchemaError:
            command = None
        if (
            command is not None
            and text[:offset]
            and command.actor_id == record.actor_id
            and command.id == record.command_id
            and command.expected_revision == record.expected_revision
        ):
            matches.append((text[:offset], command))
        offset = text.find(":{", offset + 2)
    if len(matches) != 1:
        raise ValidationError("Recorded ability principal/command identity is ambiguous or invalid")
    return matches[0]


def principal(record: CommandRecord) -> str:
    saved = CommandInput(record.payload_hash, record.command_input)
    version(saved)  # Check digest and mechanic metadata before trusting its principal.
    if saved.text is None:
        raise ValidationError("Recorded ability input is missing")
    payload = legacy_payload(record, replay_payload(saved.text))
    data = validation.mapping(payload)
    if data.get("operation") != "ability":
        raise ValidationError("Recorded ability operation changed")
    command = AbilityCommand.model_validate_json(json.dumps(data.get("command")))
    if (
        command.actor_id != record.actor_id
        or command.id != record.command_id
        or command.expected_revision != record.expected_revision
    ):
        raise ValidationError("Recorded ability mechanical identity changed")
    return validation.string(data.get("principal_id"))
