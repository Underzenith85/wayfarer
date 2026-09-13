"""Recorded spell state, ledger projections, and generic state transitions."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.magic.protocols import AreaSelection, CeremonialPlan
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "spell:"
SpellId = Literal["light", "daze", "fireball", "create-fire"]


class SpellEffect(Record):
    cast_id: Id
    actor_id: Id
    target_id: Id
    spell_id: SpellId
    build_revision: Id
    phase: Literal["casting", "active", "ended"]
    started_at: int
    ready_at: int
    expires_at: int | None = None
    skill: int
    cost: int
    maintenance: int
    hp_at_start: int
    radius: int = 1
    energy: int = 1
    distracted: bool = False
    execute_effects: bool = False
    location_id: str | None = None
    encounter_id: str | None = None
    position: tuple[int, int] | None = None
    geometry: Literal["square", "hex"] = "square"
    light_radius: int = Field(default=2, ge=0, le=100)
    light_penalty: int = Field(default=-3, ge=-9, le=0)
    concentration_seconds: int = 1
    missile_seconds: int = 1
    hp_energy: int = 0
    execution_version: Literal[1, 2] = 1
    required_turns: int | None = None
    concentrating: bool = False
    reversed: bool = False
    ceremonial: CeremonialPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    ceremonial_ht: tuple[tuple[Id, int], ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    area: AreaSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class SpellResult(Record):
    outcome: Literal[
        "casting",
        "active",
        "failed",
        "resisted",
        "cancelled",
        "interrupted",
        "critical-failure",
        "released",
        "remembered",
        "forgotten",
    ]
    energy_spent: int = 0
    hp_spent: int = Field(default=0, exclude_if=lambda value: value == 0)
    checks: tuple[CheckTrace, ...] = ()


class SpellEvent(Record):
    effect: SpellEffect
    result: SpellResult


def event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def latest(state: ResourceState) -> dict[str, SpellEffect]:
    found: dict[str, SpellEffect] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            effect = SpellEvent.model_validate_json(event.kind).effect
            found[effect.cast_id] = effect
    return found


def active_spells(state: ResourceState) -> tuple[SpellEffect, ...]:
    return tuple(
        effect
        for effect in latest(state).values()
        if effect.phase == "active"
        and (effect.expires_at is None or state.game_time < effect.expires_at)
    )


def interrupt_spells(
    state: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    events: list[ResourceEvent] = []
    for effect in latest(state).values():
        if effect.actor_id != actor_id or effect.phase != "casting":
            continue
        effect = effect.model_copy(
            update={"distracted": True} if distraction else {"phase": "ended"}
        )
        record = SpellEvent(
            effect=effect,
            result=SpellResult(outcome="casting" if distraction else "interrupted"),
        )
        events.append(
            ResourceEvent(
                id=event_id(command_id + ":interrupt:" + effect.cast_id),
                at=state.game_time,
                target_id=actor_id,
                kind=record.model_dump_json(),
            )
        )
    return state.model_copy(update={"events": state.events + tuple(events)})


def break_daze(state: ResourceState, actor_id: str, command_id: str) -> ResourceState:
    events: list[ResourceEvent] = []
    for effect in active_spells(state):
        if effect.execute_effects and effect.spell_id == "daze" and effect.target_id == actor_id:
            events.append(
                ResourceEvent(
                    id=event_id(command_id + ":daze:" + effect.cast_id),
                    at=state.game_time,
                    target_id=actor_id,
                    kind=SpellEvent(
                        effect=effect.model_copy(update={"phase": "ended"}),
                        result=SpellResult(outcome="cancelled"),
                    ).model_dump_json(),
                )
            )
    return state.model_copy(update={"events": state.events + tuple(events)})
