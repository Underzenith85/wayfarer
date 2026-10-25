"""Replay a native drowning continuation from its committed Swim producer."""

import hashlib
import json

from pydantic import Field

from wayfarer import validation
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hazard_records import HazardCommand
from wayfarer.engine.simulation.movement.physical import (
    PhysicalCommand,
    PhysicalResult,
    feat_event_id,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.physical_generations import decode, encode
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.replay import command_text


class NativeSwimResume(Record):
    producer_input: str = Field(max_length=50000)
    producer_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_result: PhysicalResult
    schedule: HazardSchedule


def recorded_swim_resume(record: CommandInput) -> NativeSwimResume | None:
    if record.text is None:
        return None
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded swimming hazard input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    source = payload.get("swim_resume")
    return NativeSwimResume.model_validate_json(json.dumps(source)) if source is not None else None


def require_swim_resume(
    state: PlayState, command: HazardCommand, captured: NativeSwimResume | None
) -> None:
    if captured is None:
        return
    if payload_digest({"input": captured.producer_input}) != captured.producer_input_digest:
        raise ValidationError("Swimming producer input does not match its digest")
    payload = validation.mapping(replay_payload(captured.producer_input))
    generation = payload.get("enchanting_rest_generation")
    if (
        payload.get("operation") != "gurps-physical"
        or type(generation) is not int
        or generation != 1
        or not isinstance(payload.get("physical_route"), str)
    ):
        raise ValidationError("Swimming continuation requires its captured native producer")
    producer = PhysicalCommand.model_validate(payload.get("command"))
    route = decode(validation.string(payload["physical_route"]))
    result = captured.producer_result
    hazard_id = "swim:" + hashlib.sha256(producer.id.encode()).hexdigest()
    schedule_id = (
        "exposure:"
        + hashlib.sha256(json.dumps([producer.actor_id, hazard_id]).encode()).hexdigest()
    )
    event = next((e for e in state.resources.events if e.id == feat_event_id(producer.id)), None)
    current = next((h for h in state.resources.hazards if h.id == schedule_id), None)
    if (
        producer.kind != "swim"
        or producer.actor_id != command.actor_id
        or producer.route_id != route.id
        or route.kind != "swim"
        or command.kind != "resolve"
        or command.hazard_id != hazard_id
        or result.succeeded
        or result.completed
        or result.seconds != 1
        or result.route_id != route.id
        or result.route_binding != encode(route)
        or event is None
        or event.target_id != command.actor_id
        or event.kind != result.model_dump_json()
        or current is None
        or not current.active
        or current != captured.schedule
        or current.actor_id != command.actor_id
        or current.started != event.at
        or current.spec
        != HazardSpec(
            id=hazard_id,
            scene_id=route.scene_id,
            kind="drowning",
            interval=5,
            cycles=100000,
            reference="B354/B436",
        )
    ):
        raise ConflictError(
            "Swimming continuation no longer matches its native producer or schedule"
        )


async def capture_swim_resume(
    play: PlayService, cid: str, command: HazardCommand, state: PlayState
) -> NativeSwimResume | None:
    replayed = recorded_command.get()
    saved = (
        CommandInput(replayed.payload_hash, replayed.command_input)
        if replayed is not None
        else await play.store.command_input(cid, command.id)
    )
    if saved is not None:
        return recorded_swim_resume(saved)
    if command.kind != "resolve" or not command.hazard_id.startswith("swim:"):
        return None
    schedule = next(
        (
            h
            for h in state.resources.hazards
            if h.actor_id == command.actor_id and h.spec.id == command.hazard_id
        ),
        None,
    )
    if schedule is None:
        return None
    # The log is the authority: a scenario-authored feat-shaped event cannot
    # create this lineage without a real committed physical command.
    producer = next(
        (
            record
            for record in await play.store.history(cid)
            if "swim:" + hashlib.sha256(record.command_id.encode()).hexdigest() == command.hazard_id
        ),
        None,
    )
    if producer is None:
        raise ValidationError("Swimming continuation requires its committed native producer")
    text = command_text(producer)
    payload = validation.mapping(replay_payload(text))
    if "enchanting_rest_generation" not in payload:
        return None  # Historical resolver-only swimmers retain their old envelope.
    captured = NativeSwimResume(
        producer_input=text,
        producer_input_digest=producer.payload_hash,
        producer_result=PhysicalResult.model_validate_json(producer.event["outcome"]),
        schedule=schedule,
    )
    require_swim_resume(state, command, captured)
    return captured
