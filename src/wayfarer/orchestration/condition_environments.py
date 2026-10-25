"""Retained trusted environment inputs for condition-producing services."""

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest


async def recorded_environment(
    play: PlayService, cid: str, command_id: str, key: str
) -> tuple[bool, object]:
    replay = recorded_command.get()
    saved = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await play.store.command_input(cid, command_id)
    )
    if saved is None:
        return False, None
    if saved.text is None:
        return True, None
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded condition input does not match its digest")
    payload = validation.mapping(replay_payload(saved.text))
    return True, payload.get(key)
