"""Private trusted route capture for physical work during enchanting rest."""

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from wayfarer import validation
from wayfarer.engine.simulation.movement.physical import PhysicalRoute, route_binding
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService

_ADAPTER = TypeAdapter(PhysicalRoute)


def encode(route: PhysicalRoute) -> str:
    return route_binding(route)


def decode(text: str) -> PhysicalRoute:
    try:
        value = validation.mapping(validation.decode(text))
        validation.fields(
            value,
            {field.name for field in fields(PhysicalRoute)} - {"use_lifting"},
            {"use_lifting"},
        )
        return _ADAPTER.validate_json(text, strict=True)
    except ValueError as exc:
        raise ValidationError("Invalid captured physical route") from exc


@dataclass(frozen=True)
class PhysicalAdmission:
    enchanting_rest: bool
    route: PhysicalRoute | None = None
    skill_minimum: bool = False


async def admission(play: PlayService, cid: str, command_id: str) -> PhysicalAdmission:
    replay = recorded_command.get()
    prior = None if replay is not None else await play.store.command_input(cid, command_id)
    if replay is None and prior is None:
        return PhysicalAdmission(True, skill_minimum=True)
    text = replay.command_input if replay is not None else prior.text if prior is not None else None
    payload = validation.mapping(replay_payload(text)) if text is not None else {}
    generation = payload.get("enchanting_rest_generation")
    minimum = payload.get("physical_skill_minimum_generation")
    if minimum is not None and (type(minimum) is not int or minimum != 1):
        raise ValidationError("Unsupported recorded physical minimum-skill generation")
    if generation is None and minimum is None:
        if "physical_route" in payload:
            raise ValidationError("Captured physical route requires its admission generation")
        return PhysicalAdmission(False)
    if generation is not None and (type(generation) is not int or generation != 1):
        raise ValidationError("Unsupported recorded physical enchanting-rest generation")
    route = payload.get("physical_route")
    if not isinstance(route, str):
        raise ValidationError("Physical admission generation requires its captured route")
    return PhysicalAdmission(generation == 1, decode(route), minimum == 1)
