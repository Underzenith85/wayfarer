"""Independent B345 admission generation retained by deferred check producers."""

import hashlib

from wayfarer.engine.simulation.campaign.skill_context_state import CapturedSkillContext, Producer
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError

PREFIX = "minimum-effective-skill:"


def identifier(producer: Producer, command_id: str) -> str:
    return PREFIX + hashlib.sha256(f"{producer}:{command_id}".encode()).hexdigest()


def mark(
    resources: ResourceState, producer: Producer, command_id: str, actor_id: str, *, enabled: bool
) -> ResourceState:
    if not enabled:
        return resources
    event_id = identifier(producer, command_id)
    if any(e.id == event_id for e in resources.events):
        raise ConflictError("Minimum-skill producer identity already exists")
    # Same strict bound-version record contract; a different namespace makes
    # the floor independent of the earlier prerequisite-context generation.
    value = CapturedSkillContext(
        producer=producer, command_id=command_id, actor_id=actor_id, generation=1
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def enabled(resources: ResourceState, producer: Producer, command_id: str, actor_id: str) -> bool:
    event = next((e for e in resources.events if e.id == identifier(producer, command_id)), None)
    if event is None:
        return False
    value = CapturedSkillContext.model_validate_json(event.kind)
    expected = CapturedSkillContext(
        producer=producer, command_id=command_id, actor_id=actor_id, generation=1
    )
    if value != expected or event.target_id != actor_id or event.at > resources.game_time:
        raise ValidationError("Captured minimum skill does not match its deferred producer")
    return True
