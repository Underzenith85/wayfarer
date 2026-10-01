"""Private, immutable physical construction facts for B240/B481 magic staffs."""

import hashlib

from wayfarer.engine.rules.magic.protocols import MagicStaff
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id

PREFIX = "staff-construction:"


class StaffConstruction(MagicStaff):
    definition_id: Id


class DeclareStaffConstruction(Command):
    construction: StaffConstruction


def identifier(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def constructions(state: ResourceState) -> tuple[StaffConstruction, ...]:
    return tuple(
        StaffConstruction.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )


def _validate(state: ResourceState, value: StaffConstruction) -> None:
    item = next((i for i in state.items if i.id == value.item_id), None)
    if (
        item is None
        or item.definition_id != value.definition_id
        or item.quantity != 1
        or (item.condition is not None and item.condition.disabled)
    ):
        raise ValidationError("Staff construction requires the current intact individual item")
    if not value.once_living:
        raise ValidationError("A magic staff must be made from once-living material")


def declare(state: ResourceState, command: DeclareStaffConstruction) -> ResourceState:
    value = command.construction
    if any(c.item_id == value.item_id for c in constructions(state)):
        raise ConflictError("A staff's physical construction cannot be replaced")
    _validate(state, value)
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=identifier(command.id),
                    at=state.game_time,
                    target_id=value.item_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def require_staff_construction(state: ResourceState, item_id: str) -> StaffConstruction:
    value = next((c for c in constructions(state) if c.item_id == item_id), None)
    if value is None:
        raise ValidationError("Staff enchantment requires trusted physical construction")
    _validate(state, value)
    return value


def validate_staff_target(state: ResourceState, item_id: str) -> None:
    require_staff_construction(state, item_id)
