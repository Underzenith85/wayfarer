"""Capture authenticated private Great Haste timing; absence preserves legacy."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer import validation
from wayfarer.engine.simulation.magic.great_haste_named import NamedCastGreatHaste
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    InitialStepCastGreatHaste,
    NamedInitialStepCastGreatHaste,
    NamedOngoingStepCastGreatHaste,
    NamedStepCastGreatHaste,
    OngoingStepCastGreatHaste,
    StepCastGreatHaste,
)
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
    generation = payload.get("generation")
    command = validation.mapping(payload.get("command"))
    step = command.get("kind") == "step-great-haste"
    if (
        type(generation) is not int
        or generation not in (1, 2, 3, 4, 5, 6, 7, 8, 9)
        or (generation in (2, 5)) != step
        or (generation == 3) != (command.get("kind") == "named-great-haste")
        or (generation == 4) != (command.get("kind") == "named-step-great-haste")
        or (generation == 6) != (command.get("kind") == "initial-step-great-haste")
        or (generation == 7) != (command.get("kind") == "named-initial-step-great-haste")
        or (generation == 8) != (command.get("kind") == "ongoing-step-great-haste")
        or (generation == 9) != (command.get("kind") == "named-ongoing-step-great-haste")
    ):
        raise ValidationError("Unsupported Great Haste casting generation")
    if generation in (2, 5):
        try:
            StepCastGreatHaste.model_validate_json(json.dumps(command, sort_keys=True))
        except SchemaError as error:
            raise ValidationError("Invalid selected-Step casting generation") from error
    if generation == 3:
        try:
            NamedCastGreatHaste.model_validate_json(json.dumps(command, sort_keys=True))
        except SchemaError as error:
            raise ValidationError("Invalid named casting generation") from error
    if generation == 4:
        try:
            NamedStepCastGreatHaste.model_validate_json(json.dumps(command, sort_keys=True))
        except SchemaError as error:
            raise ValidationError("Invalid named selected-Step casting generation") from error
    if generation in (6, 7):
        if KEY not in payload:
            raise ValidationError("Initial selected-Step requires authenticated casting generation")
        try:
            model = InitialStepCastGreatHaste if generation == 6 else NamedInitialStepCastGreatHaste
            model.model_validate_json(json.dumps(command, sort_keys=True))
        except SchemaError as error:
            raise ValidationError("Invalid initial selected-Step casting generation") from error
    if generation in (8, 9):
        if KEY not in payload:
            raise ValidationError("Ongoing selected-Step requires authenticated casting generation")
        try:
            ongoing_model = (
                OngoingStepCastGreatHaste if generation == 8 else NamedOngoingStepCastGreatHaste
            )
            ongoing_model.model_validate_json(json.dumps(command, sort_keys=True))
        except SchemaError as error:
            raise ValidationError("Invalid ongoing selected-Step casting generation") from error
    return KEY in payload


async def ritual_steps(store: Store, cid: str, command_id: str) -> bool:
    prior = recorded_command.get()
    record = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior
        else await store.command_input(cid, command_id)
    )
    if record is None:
        return True
    if record.text is None:
        return False
    features(record)
    return validation.mapping(replay_payload(record.text)).get("generation") in (4, 5, 6, 7, 8, 9)
