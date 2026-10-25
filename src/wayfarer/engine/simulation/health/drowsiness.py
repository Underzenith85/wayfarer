"""Private per-exposure generation for B428 toxin drowsiness check modifiers."""

import hashlib

from wayfarer.engine.simulation.magic.awaken_state import alert_until
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError


def active_sources(state: ResourceState, actor_id: str) -> tuple[str, ...]:
    """B35/B428: several sources sustain one named Drowsy condition."""
    survival = next((s for s in state.survival if s.actor_id == actor_id), None)
    sources: tuple[str, ...] = (
        ("survival:missed-sleep",)
        if survival is not None
        and survival.drowsy_until is not None
        and survival.drowsy_until > state.game_time
        and not alert_until(state, actor_id)
        else ()
    )
    for toxin in state.toxins:
        if (
            toxin.actor_id == actor_id
            and toxin.profile.condition == "drowsy"
            and toxin.condition_until > state.game_time
        ):
            current(state, toxin.id, actor_id)
            sources += ("toxin:" + toxin.id,)
    return sources


def event_id(exposure_id: str) -> str:
    return "toxin-drowsiness:" + hashlib.sha256(exposure_id.encode()).hexdigest()


def current(state: ResourceState, exposure_id: str, actor_id: str) -> bool:
    markers = tuple(e for e in state.events if e.id == event_id(exposure_id))
    if len(markers) > 1 or markers and (markers[0].kind != "1" or markers[0].target_id != actor_id):
        raise ValidationError("Unsupported recorded toxin drowsiness generation")
    return bool(markers)


def capture(state: ResourceState, exposure_id: str, actor_id: str) -> ResourceState:
    """New supported producers explicitly bind the corrected condition semantics."""
    if current(state, exposure_id, actor_id):
        raise ValidationError("Toxin drowsiness generation is already captured")
    if not any(e.id == exposure_id and e.actor_id == actor_id for e in state.toxins):
        raise ValidationError("Toxin drowsiness generation requires its actual exposure")
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id(exposure_id),
                    at=state.game_time,
                    target_id=actor_id,
                    kind="1",
                ),
            )
        }
    )
