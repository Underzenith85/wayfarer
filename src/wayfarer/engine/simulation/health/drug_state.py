"""Private waking facts leave ongoing drug exposures intact (B248, B440-441)."""

import hashlib
import json
from typing import Literal

from wayfarer.engine.rules.types.toxin import ToxinExposure
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Record

PREFIX = "drug-wake:"
type DrugKind = Literal["toxin", "intoxication"]


class DrugWake(Record):
    actor_id: str
    kind: DrugKind
    target_id: str
    awake: bool


def _overrides(state: ResourceState) -> dict[tuple[DrugKind, str], bool]:
    result = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            wake = DrugWake.model_validate_json(event.kind)
            result[(wake.kind, wake.target_id)] = wake.awake
    return result


def _toxin_unconscious(toxin: ToxinExposure, at: int) -> bool:
    return toxin.overdose_until > at or (
        toxin.condition_until > at and toxin.profile.condition == "unconscious"
    )


def drugged(state: ResourceState, actor_id: str) -> bool:
    return any(
        toxin.actor_id == actor_id
        and (
            toxin.overdose_until > state.game_time
            or (toxin.condition_until > state.game_time and toxin.profile.condition != "none")
        )
        for toxin in state.toxins
    ) or any(i.actor_id == actor_id and i.level != "sober" for i in state.intoxications)


def drug_unconscious(state: ResourceState, actor_id: str) -> bool:
    overrides = _overrides(state)
    return any(
        toxin.actor_id == actor_id
        and not overrides.get(("toxin", toxin.id), False)
        and _toxin_unconscious(toxin, state.game_time)
        for toxin in state.toxins
    ) or any(
        i.actor_id == actor_id
        and not overrides.get(("intoxication", i.actor_id), False)
        and i.level in ("unconscious", "coma")
        for i in state.intoxications
    )


def _record(state: ResourceState, wake: DrugWake, command_id: str) -> ResourceState:
    marker = hashlib.sha256(
        json.dumps([command_id, wake.kind, wake.target_id]).encode()
    ).hexdigest()
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + marker,
                    at=state.game_time,
                    target_id=wake.actor_id,
                    kind=wake.model_dump_json(),
                ),
            ),
        }
    )


def invalidate_waking(
    state: ResourceState,
    actor_id: str,
    kind: DrugKind,
    target_id: str,
    command_id: str,
    *,
    incapacitating: bool,
) -> ResourceState:
    """A fresh incapacitating effect invalidates only its own existing wake override."""
    if not incapacitating or not _overrides(state).get((kind, target_id), False):
        return state
    return _record(
        state, DrugWake(actor_id=actor_id, kind=kind, target_id=target_id, awake=False), command_id
    )


def wake_drugged(state: ResourceState, actor_id: str, command_id: str) -> ResourceState:
    """Leave toxicity, intoxication, symptoms, and their deadlines in force."""
    for toxin in state.toxins:
        if toxin.actor_id == actor_id and _toxin_unconscious(toxin, state.game_time):
            state = _record(
                state,
                DrugWake(actor_id=actor_id, kind="toxin", target_id=toxin.id, awake=True),
                command_id,
            )
    for item in state.intoxications:
        if item.actor_id == actor_id and item.level in ("unconscious", "coma"):
            state = _record(
                state,
                DrugWake(actor_id=actor_id, kind="intoxication", target_id=actor_id, awake=True),
                command_id,
            )
    return state
