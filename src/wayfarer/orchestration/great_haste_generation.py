"""Capture authenticated private Great Haste timing; absence preserves legacy."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import great_haste_intent, replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "great_haste_combat_generation"
ORIGINAL = "great_haste_original_input"


async def capture(store: Store, cid: str, command_id: str) -> bool:
    prior = recorded_command.get()
    record = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior
        else await store.command_input(cid, command_id)
    )
    return True if record is None else features(record)


def features(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded Great Haste input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    if payload.get("operation") != "great-haste":
        raise ValidationError("Recorded Great Haste operation changed")
    # Validate both generation and its exact original request before admission.
    great_haste_intent(json.dumps(payload, sort_keys=True))
    return KEY in payload
