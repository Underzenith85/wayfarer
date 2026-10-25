"""Origin-pinned default knowledge adoption for attacks and Wait continuations."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import field_validator

from wayfarer.engine.simulation.campaign.default_knowledge import PROFILE, adopted
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.encounter import Encounter
    from wayfarer.engine.simulation.rules_context import RulesContext

PREFIX = "skill-default-policy:"


class DefaultKnowledgePolicy(Record):
    encounter_id: Id
    actor_id: Id
    producer_id: Id
    generation: Literal[1, 2] = 1

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> int:
        if type(value) is not int or value not in (1, 2):
            raise ValueError("Unsupported captured default knowledge policy")
        return value


def identifier(encounter_id: str, actor_id: str, producer_id: str) -> str:
    return (
        PREFIX
        + hashlib.sha256(json.dumps([encounter_id, actor_id, producer_id]).encode()).hexdigest()
    )


def _recorded(state: PlayState, encounter_id: str, actor_id: str, producer_id: str) -> int:
    key = identifier(encounter_id, actor_id, producer_id)
    events = tuple(event for event in state.resources.events if event.id == key)
    if not events:
        return 0
    if len(events) != 1:
        raise ValidationError("Captured default knowledge policy identity is duplicated")
    event = events[0]
    value = DefaultKnowledgePolicy.model_validate_json(event.kind)
    if (
        value
        != DefaultKnowledgePolicy(
            encounter_id=encounter_id,
            actor_id=actor_id,
            producer_id=producer_id,
            generation=value.generation,
        )
        or event.target_id != actor_id
        or event.at > state.resources.game_time
    ):
        raise ValidationError("Captured default knowledge policy does not match its producer")
    return value.generation


def _wait_id(state: PlayState, actor_id: str) -> str:
    hp = next(pool for pool in state.resources.pools if pool.id == "hp:" + actor_id)
    assert hp.injury is not None
    return "wait-turn:" + str(hp.injury.turn)


def initiating(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    """An old pending producer stays legacy despite later actor adoption."""
    if runtime.reviewer.compiler.statistics_profile != PROFILE:
        return 0
    for encounter in state.encounters:
        if encounter.wait_interrupt is not None and encounter.wait_interrupt.waiter_id == actor_id:
            return _recorded(state, encounter.id, actor_id, _wait_id(state, actor_id))
        pending = encounter.pending_defense
        if pending is not None and pending.attacker_id == actor_id:
            return _recorded(state, encounter.id, actor_id, pending.id)
    if runtime.require_default_knowledge:
        return 2
    return int(runtime.correct_default_knowledge and adopted(state, actor_id))


def active(runtime: RulesContext, state: PlayState, actor_id: str) -> bool:
    # Original impact consumes its recorded target; continuation producers still
    # inherit the initiating policy through initiating(), even across a pause.
    if any(
        encounter.pending_defense is not None
        and encounter.pending_defense.attacker_id == actor_id
        and encounter.pending_defense.attack_roll is not None
        for encounter in state.encounters
    ):
        return False
    return initiating(runtime, state, actor_id) > 0


def required(runtime: RulesContext, state: PlayState, actor_id: str) -> bool:
    return active(runtime, state, actor_id) and initiating(runtime, state, actor_id) >= 2


def bind(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    wait_actor_id: str | None = None,
    initiating_state: PlayState | None = None,
) -> PlayState:
    pending = encounter.pending_defense
    actor_id = wait_actor_id or (pending.attacker_id if pending is not None else None)
    if (
        actor_id is None
        or runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004"
        or not initiating(runtime, initiating_state or state, actor_id)
    ):
        return state
    producer_id = (
        _wait_id(state, actor_id)
        if wait_actor_id is not None
        else pending.id
        if pending is not None
        else ""
    )
    if _recorded(state, encounter.id, actor_id, producer_id):
        return state
    value = DefaultKnowledgePolicy(
        encounter_id=encounter.id,
        actor_id=actor_id,
        producer_id=producer_id,
        generation=2 if initiating(runtime, initiating_state or state, actor_id) >= 2 else 1,
    )
    event = ResourceEvent(
        id=identifier(encounter.id, actor_id, producer_id),
        at=state.resources.game_time,
        target_id=actor_id,
        kind=value.model_dump_json(),
    )
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + (event,)}
            )
        }
    )
