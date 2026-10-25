"""Independent start policy; treatment completion reads its captured producer."""

from dataclasses import dataclass

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import intent_input
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.medical_minimum_inputs import ENVIRONMENT, KEY, intent, validate_keys

ACTIVE = True


@dataclass(frozen=True)
class MinimumInputs:
    enabled: bool
    saved: bool
    environment: object = None


def read(saved: CommandInput, *, eligible: bool, command_id: str, actor_id: str) -> MinimumInputs:
    if saved.text is None:
        return MinimumInputs(False, True)
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded medical minimum input does not match its digest")
    validate_keys(saved.text)
    raw = intent_input(saved.text)
    intent(raw)
    payload = validation.mapping(validation.decode(raw))
    if KEY not in payload:
        return MinimumInputs(False, True)
    command = validation.mapping(payload.get("command"))
    if not eligible or command.get("id") != command_id or command.get("actor_id") != actor_id:
        raise ValidationError("Medical minimum generation does not match its producer")
    return MinimumInputs(True, True, payload[ENVIRONMENT])


async def capture(
    store: Store, cid: str, command_id: str, *, eligible: bool, actor_id: str
) -> MinimumInputs:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    return (
        MinimumInputs(ACTIVE and eligible, False)
        if saved is None
        else read(saved, eligible=eligible, command_id=command_id, actor_id=actor_id)
    )
