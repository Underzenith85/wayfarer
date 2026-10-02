"""Private grenade-fuse capture for an actual ordinary opponent task choice."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer import validation
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.errors import ValidationError
from wayfarer.orchestration.opponent_attack_records import ChooseOpponentAttack
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import combat_intent, intent_input, replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "task_combat_protocol_features"
ACTIVE = frozenset({"grenade-fuse"})


def applicable(command: object) -> bool:
    return isinstance(command, ChooseOpponentAttack) and isinstance(command.response, ChooseDefense)


def producer(record: CommandInput) -> ChooseOpponentAttack:
    if record.text is None or payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded task combat input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    if payload.get("operation") != "task-host":
        raise ValidationError("Task combat features require their registered host operation")
    try:
        command = ChooseOpponentAttack.model_validate_json(json.dumps(payload.get("command")))
    except SchemaError as exc:
        raise ValidationError("Task combat features require a canonical opponent choice") from exc
    if not applicable(command):
        raise ValidationError("Task combat features require an actual ordinary defense")
    return command


def features(record: CommandInput) -> frozenset[str]:
    if record.text is None:
        return frozenset()
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded task combat input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    if KEY not in payload:
        return frozenset()
    producer(record)
    # Validate exact canonical bytes, feature names/types and the private-layer boundary.
    combat_intent(intent_input(record.text))
    raw = payload[KEY]
    assert isinstance(raw, list)
    return frozenset(validation.string(item) for item in raw)


async def capture(store: Store, cid: str, command: object, command_id: str) -> frozenset[str]:
    prior = recorded_command.get()
    record = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    selected = (
        ACTIVE
        if record is None and applicable(command)
        else features(record)
        if record
        else frozenset()
    )
    if selected and not applicable(command):
        raise ValidationError("Captured task combat features do not match the requested route")
    return selected
