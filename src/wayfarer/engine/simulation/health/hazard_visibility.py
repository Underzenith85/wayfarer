"""Private audience binding for one source-verified secret hazard continuation."""

import hashlib

from wayfarer.engine.simulation.health.hazard_records import HazardResult
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "outside-secret-hazard:"


class SecretHazardResult(Record):
    command_id: Id
    actor_id: Id
    schedule_id: Id
    exposure_command_id: Id


def _id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def conceal_hazard_result(
    state: ResourceState,
    binding: SecretHazardResult,
    *,
    system: bool = False,
) -> ResourceState:
    if not system:
        raise ValidationError("Secret hazard projection requires trusted source authority")
    identifier = _id(binding.command_id)
    previous = next((event for event in state.events if event.id == identifier), None)
    if previous is not None:
        if SecretHazardResult.model_validate_json(previous.kind) != binding:
            raise ConflictError("Secret hazard audience was already recorded differently")
        return state
    marker = ResourceEvent(
        id=identifier,
        at=state.game_time,
        target_id=binding.actor_id,
        kind=binding.model_dump_json(),
    )
    updated = state.model_copy(update={"events": state.events + (marker,)})
    event = next(
        (event for event in state.events if event.id == "hazard:" + binding.command_id), None
    )
    if event is None or not private_hazard_result(updated, event):
        raise ValidationError("Secret audience has no matching actual hazard consequence")
    return updated


def private_hazard_result(state: ResourceState, event: ResourceEvent) -> bool:
    command_id = event.id.removeprefix("hazard:")
    marker = next((row for row in state.events if row.id == _id(command_id)), None)
    if marker is None:
        return False
    binding = SecretHazardResult.model_validate_json(marker.kind)
    result = HazardResult.model_validate_json(event.kind)
    source = next(
        (row for row in state.events if row.id == "hazard:" + binding.exposure_command_id), None
    )
    if (
        binding.command_id != command_id
        or binding.actor_id != event.target_id
        or binding.actor_id != marker.target_id
        or binding.schedule_id != result.schedule_id
        or source is None
        or source.target_id != binding.actor_id
        or HazardResult.model_validate_json(source.kind).schedule_id != binding.schedule_id
    ):
        raise ValidationError("Secret hazard audience does not match its exact source and result")
    return True


def private_hazard_command(state: ResourceState, command_id: str) -> bool:
    """Apply the exact result's audience to its enclosing command receipt too."""
    event = next((row for row in state.events if row.id == "hazard:" + command_id), None)
    return event is not None and private_hazard_result(state, event)
