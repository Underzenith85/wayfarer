"""Bind a natural-event declaration to immutable, real environmental admission."""

import hashlib
import json

from wayfarer import validation
from wayfarer.engine.rules.types.hazard import HazardSchedule
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hazards import HazardCommand, HazardResult
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.outside_event_records import (
    NaturalExposureDeclaration,
    PrepareOutsideEvent,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.orchestration.task_records import TaskCommand
from wayfarer.persistence.events import CommandInput, payload_digest


def _payload(record: CommandInput) -> dict[str, object]:
    if record.text is None or payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Outside event source input is missing or does not match its digest")
    decoded = replay_payload(record.text)
    return validation.mapping(decoded) if isinstance(decoded, dict) else {}


async def bind_outside_event_source(
    play: PlayService, cid: str, command: TaskCommand
) -> TaskCommand:
    if not isinstance(command, PrepareOutsideEvent):
        return command
    replayed = recorded_command.get()
    prior = (
        CommandInput(payload_hash=replayed.payload_hash, text=replayed.command_input)
        if replayed is not None
        else await play.store.command_input(cid, command.id)
    )
    if prior is not None:
        old_payload = _payload(prior)
        old = old_payload.get("command")
        if (
            old_payload.get("operation") == "task-host"
            and isinstance(old, dict)
            and old.get("kind") == "prepare-outside-event"
        ):
            captured = PrepareOutsideEvent.model_validate_json(json.dumps(old))
            if (
                command.exposure_command is not None
                and command.exposure_command != captured.exposure_command
            ):
                raise ConflictError("Recorded outside-event source changed")
            return command.model_copy(update={"exposure_command": captured.exposure_command})
        raise ConflictError("Outside event command identity already belongs to another operation")
    source_input = await play.store.command_input(cid, command.source.exposure_command_id)
    if source_input is None:
        raise ValidationError(
            "Natural outside events require their recorded environmental admission"
        )
    source = _payload(source_input)
    if source.get("operation") != "gurps-hazard" or not isinstance(source.get("command"), dict):
        raise ValidationError("Natural outside events cannot borrow an attack or spell source")
    exposure = HazardCommand.model_validate_json(json.dumps(source["command"]))
    if command.exposure_command is not None and command.exposure_command != exposure:
        raise ConflictError("Supplied outside-event source differs from its recorded admission")
    return command.model_copy(update={"exposure_command": exposure})


def validate_natural_exposure(
    state: PlayState,
    actor_id: str,
    schedule: HazardSchedule,
    source: NaturalExposureDeclaration,
    command: HazardCommand | None,
) -> None:
    if command is None or (
        command.kind != "enter"
        or command.actor_id != actor_id
        or command.hazard_id != schedule.spec.id
        or command.id != source.exposure_command_id
    ):
        raise ValidationError("Natural source must name this owner's actual exposure admission")
    expected_id = (
        "exposure:" + hashlib.sha256(json.dumps([actor_id, command.hazard_id]).encode()).hexdigest()
    )
    if schedule.id != expected_id or schedule.spec.id.startswith(
        ("spell-fire:", "spell-crossing:", "sprayer-fire:")
    ):
        raise ValidationError("Attack or spell exposure is not a declared natural outside event")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    if not any(
        receipt.command_id == command.id and receipt.digest == digest
        for receipt in state.resources.receipts
    ):
        raise ValidationError("Natural exposure has no matching canonical admission receipt")
    event = next(
        (event for event in state.resources.events if event.id == "hazard:" + command.id), None
    )
    if event is None:
        raise ValidationError("Natural exposure has no recorded admission consequence")
    result = HazardResult.model_validate_json(event.kind)
    if (
        result.schedule_id != schedule.id
        or result.hp_lost
        or result.fp_lost
        or result.check is not None
    ):
        raise ValidationError("Natural event source is not the original exposure admission")
