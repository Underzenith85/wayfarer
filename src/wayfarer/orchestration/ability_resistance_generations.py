"""Capture B348 separately from Rule16, off-shift and current GM facts."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer import validation
from wayfarer.engine.simulation.ability_resistance_origin import current
from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.ability_resistance_inputs import KEY, intent, stamp
from wayfarer.persistence.command_inputs import intent_input, replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest


def version(saved: CommandInput) -> bool:
    if saved.text is None:
        return False
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded ability resistance input changed")
    data = replay_payload(saved.text)
    intent(intent_input(saved.text))
    if not isinstance(data, dict) or KEY not in data:
        return False
    try:
        command = AbilityCommand.model_validate_json(json.dumps(data.get("command")))
    except SchemaError as exc:
        raise ValidationError("Invalid recorded ability resistance command") from exc
    if command.kind not in {"activate", "resolve"}:
        raise ValidationError("Resistance policy belongs to an attempted activation")
    validation.string(data.get("principal_id"))
    return True


async def capture(
    store: Store,
    cid: str,
    command: AbilityCommand,
    resources: ResourceState,
    *,
    eligible: bool,
) -> bool:
    replay = recorded_command.get()
    saved = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command.id)
    )
    if saved is not None:
        return version(saved)
    if not eligible:
        return False
    return command.kind == "activate" or command.kind == "resolve" and current(resources, command)


def payload(base: str, principal: str, command: AbilityCommand, enabled: bool) -> str:
    if enabled and command.kind not in {"activate", "resolve"}:
        raise ValidationError("Resistance policy belongs to an attempted activation")
    return stamp(base, principal, command.model_dump(mode="json")) if enabled else base
