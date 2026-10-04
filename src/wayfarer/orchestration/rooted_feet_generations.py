"""Capture private Rooted admission policy from the original accepted command."""

from wayfarer import validation
from wayfarer.engine.simulation.magic.rooted_feet_policy import RootedGeneration
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

CURRENT: RootedGeneration = 2


def generation(record: CommandInput) -> RootedGeneration:
    if record.text is None:
        raise ValidationError("Rooted Feet replay requires its captured admission policy")
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded Rooted input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    value = payload.get("generation")
    if payload.get("operation") != "rooted-feet" or type(value) is not int:
        raise ValidationError("Invalid recorded Rooted admission generation")
    if value == 1:
        return 1
    if value == 2:
        return 2
    raise ValidationError("Unsupported recorded Rooted admission generation")


async def capture(store: Store, cid: str, command_id: str) -> RootedGeneration:
    prior = recorded_command.get()
    record = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    return CURRENT if record is None else generation(record)
