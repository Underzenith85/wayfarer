"""Independent strict recorded B69 off-shift producer policy."""

from wayfarer import validation
from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import canonical, replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "ability_offshift_generation"


def version(saved: CommandInput) -> bool:
    if saved.text is None:
        return False
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded off-shift ability input changed")
    decoded = replay_payload(saved.text)
    if not isinstance(decoded, dict) or KEY not in decoded:
        return False
    data = validation.mapping(decoded)
    if type(data[KEY]) is not int or data[KEY] != 1 or data.get("operation") != "ability":
        raise ValidationError("Unsupported off-shift ability generation")
    command = validation.mapping(data.get("command"))
    if command.get("kind") != "activate":
        raise ValidationError("Off-shift policy belongs to ability activation")
    return True


async def capture(store: Store, cid: str, command_id: str, *, eligible: bool) -> bool:
    replay = recorded_command.get()
    saved = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command_id)
    )
    return eligible if saved is None else version(saved)


def payload(base: str, principal: str, command: AbilityCommand, enabled: bool) -> str:
    if not enabled:
        return base
    if command.kind != "activate":
        raise ValidationError("Off-shift policy belongs to ability activation")
    data = replay_payload(base)
    envelope = (
        dict(data)
        if isinstance(data, dict)
        else {
            "operation": "ability",
            "principal_id": principal,
            "command": command.model_dump(mode="json"),
        }
    )
    return canonical({**envelope, KEY: 1})
