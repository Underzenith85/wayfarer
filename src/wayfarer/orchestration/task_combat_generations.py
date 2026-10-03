"""Private grenade-fuse capture for an actual ordinary task defense producer."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer import validation
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.errors import ValidationError
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.opponent_fragment_records import (
    AmendFragmentResponses,
    ChooseOpponentFragment,
    PrepareOpponentFragment,
)
from wayfarer.orchestration.owner_damage_records import PrepareOwnerDamage
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import combat_intent, intent_input, replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "task_combat_protocol_features"
ACTIVE = frozenset({"grenade-fuse", "missile-interposition"})
BEGIN_ACTIVE = frozenset({"missile-interposition"})
FRAGMENT_ACTIVE = frozenset({"ground-dive-step", "secondary-object-blasts"})
FRAGMENT_MODELS = (PrepareOpponentFragment, ChooseOpponentFragment, AmendFragmentResponses)


def fragment_applicable(command: object) -> bool:
    return isinstance(command, FRAGMENT_MODELS)


def fragment_producer(payload: dict[str, object]) -> None:
    raw = validation.mapping(payload.get("command"))
    model = next(
        (
            model
            for model in FRAGMENT_MODELS
            if model.model_fields["kind"].default == raw.get("kind")
        ),
        None,
    )
    if model is None:
        raise ValidationError("Task fragment features require a canonical fragment route")
    try:
        model.model_validate_json(json.dumps(raw))
    except SchemaError as exc:
        raise ValidationError("Task fragment features require a typed fragment command") from exc


def applicable(command: object) -> bool:
    return isinstance(command, BeginOpponentAttack) or (
        isinstance(command, (ChooseOpponentAttack, PrepareOwnerDamage))
        and isinstance(command.response, ChooseDefense)
    )


def producer(
    record: CommandInput,
) -> BeginOpponentAttack | ChooseOpponentAttack | PrepareOwnerDamage:
    if record.text is None or payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded task combat input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    if payload.get("operation") != "task-host":
        raise ValidationError("Task combat features require their registered host operation")
    try:
        raw = validation.mapping(payload.get("command"))
        model = (
            BeginOpponentAttack
            if raw.get("kind") == "begin-opponent-attack"
            else PrepareOwnerDamage
            if raw.get("kind") == "prepare-owner-damage"
            else ChooseOpponentAttack
        )
        command = model.model_validate_json(json.dumps(raw))
    except SchemaError as exc:
        raise ValidationError(
            "Task combat features require a canonical task defense producer"
        ) from exc
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
    raw_command = validation.mapping(payload.get("command"))
    if raw_command.get("kind") in tuple(
        model.model_fields["kind"].default for model in FRAGMENT_MODELS
    ):
        fragment_producer(payload)
    else:
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
        (
            FRAGMENT_ACTIVE
            if fragment_applicable(command)
            else BEGIN_ACTIVE
            if isinstance(command, BeginOpponentAttack)
            else ACTIVE
        )
        if record is None and (applicable(command) or fragment_applicable(command))
        else features(record)
        if record
        else frozenset()
    )
    if selected and not (applicable(command) or fragment_applicable(command)):
        raise ValidationError("Captured task combat features do not match the requested route")
    return selected
