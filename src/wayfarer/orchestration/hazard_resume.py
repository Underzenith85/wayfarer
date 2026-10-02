"""Capture replayable ordinary continuations of admitted natural exposures."""

import json

from pydantic import Field

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hazard_damage import (
    PREREQUISITE_PREFIX,
    NaturalExposureDeclaration,
    OutsidePrerequisite,
)
from wayfarer.engine.simulation.health.hazard_records import HazardCommand
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record
from wayfarer.orchestration.outside_event_sources import validate_natural_exposure
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest


class NaturalHazardResume(Record):
    schedule_id: Id
    cycle: int = Field(ge=0)
    due: int = Field(ge=0)
    source: NaturalExposureDeclaration
    exposure_command: HazardCommand


def current_resume(state: PlayState, command: HazardCommand) -> NaturalHazardResume | None:
    if command.kind != "resolve":
        return None
    schedule = next(
        (
            row
            for row in state.resources.hazards
            if row.actor_id == command.actor_id and row.spec.id == command.hazard_id
        ),
        None,
    )
    if schedule is None:
        return None
    for event in reversed(state.resources.events):
        if not event.id.startswith(PREREQUISITE_PREFIX):
            continue
        prior = OutsidePrerequisite.model_validate_json(event.kind)
        if prior.preparation.schedule.id == schedule.id:
            validate_natural_exposure(
                state, command.actor_id, schedule, prior.source, prior.exposure_command
            )
            return NaturalHazardResume(
                schedule_id=schedule.id,
                cycle=schedule.cycle,
                due=schedule.due,
                source=prior.source,
                exposure_command=prior.exposure_command,
            )
    return None


def recorded_resume(record: CommandInput) -> NaturalHazardResume | None:
    if record.text is None:
        return None
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded hazard input does not match its digest")
    decoded = replay_payload(record.text)
    value = validation.mapping(decoded) if isinstance(decoded, dict) else {}
    source = value.get("outside_resume")
    return (
        NaturalHazardResume.model_validate_json(json.dumps(source)) if source is not None else None
    )


async def capture_hazard_resume(
    play: PlayService,
    cid: str,
    command: HazardCommand,
    state: PlayState,
) -> NaturalHazardResume | None:
    replayed = recorded_command.get()
    saved = (
        CommandInput(payload_hash=replayed.payload_hash, text=replayed.command_input)
        if replayed is not None
        else await play.store.command_input(cid, command.id)
    )
    return recorded_resume(saved) if saved is not None else current_resume(state, command)


def require_hazard_resume(
    state: PlayState, command: HazardCommand, captured: NaturalHazardResume | None
) -> None:
    if captured is not None and current_resume(state, command) != captured:
        raise ConflictError("Recorded outside hazard continuation no longer matches its source")
