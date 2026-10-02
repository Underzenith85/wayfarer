"""Recorded spell state, ledger projections, and generic state transitions."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.magic.protocols import (
    AreaSelection,
    CeremonialPlan,
    HeldSpellDisposition,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "spell:"
RUNTIME_PREFIX = "runtime-spell:"
PRIVATE_SPELLS = frozenset({"lockmaster", "magelock", "haste", "apportation"})
SpellId = Literal[
    "awaken",
    "light",
    "daze",
    "fireball",
    "create-fire",
    "minor-healing",
    "major-healing",
    "great-healing",
    "lend-energy",
    "lend-vitality",
]


RuntimeSpellId = Literal[SpellId, "lockmaster", "magelock", "haste", "apportation"]


class RuntimeSpellEffect(Record):
    """Private engine vocabulary; legacy event contracts keep their closed facade."""

    cast_id: Id
    actor_id: Id
    target_id: Id
    spell_id: RuntimeSpellId
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


class SpellEffect(RuntimeSpellEffect):
    spell_id: SpellId


class SpellResult(Record):
    outcome: Literal[
        "casting",
        "active",
        "failed",
        "resisted",
        "cancelled",
        "dissipated",
        "dropped",
        "interrupted",
        "critical-failure",
        "released",
        "remembered",
        "forgotten",
    ]
    fp_restored: int = Field(default=0, exclude_if=lambda value: value == 0)
    hp_restored: int = Field(default=0, exclude_if=lambda value: value == 0)
    energy_spent: int = 0
    hp_spent: int = Field(default=0, exclude_if=lambda value: value == 0)
    checks: tuple[CheckTrace, ...] = ()
    held_disposition: HeldSpellDisposition | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class RuntimeSpellEvent(Record):
    effect: RuntimeSpellEffect
    result: SpellResult


class SpellEvent(RuntimeSpellEvent):
    effect: SpellEffect


def event_id(command_id: str, spell_id: str | None = None) -> str:
    prefix = RUNTIME_PREFIX if spell_id in PRIVATE_SPELLS else PREFIX
    return prefix + hashlib.sha256(command_id.encode()).hexdigest()


def parse_event(event: ResourceEvent) -> RuntimeSpellEvent:
    model = RuntimeSpellEvent if event.id.startswith(RUNTIME_PREFIX) else SpellEvent
    return model.model_validate_json(event.kind)


def latest(state: ResourceState) -> dict[str, RuntimeSpellEffect]:
    found: dict[str, RuntimeSpellEffect] = {}
    for event in state.events:
        if event.id.startswith((PREFIX, RUNTIME_PREFIX)):
            effect = parse_event(event).effect
            found[effect.cast_id] = effect
    return found


def active_spells(state: ResourceState) -> tuple[RuntimeSpellEffect, ...]:
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
        record = RuntimeSpellEvent(
            effect=effect,
            result=SpellResult(outcome="casting" if distraction else "interrupted"),
        )
        events.append(
            ResourceEvent(
                id=event_id(command_id + ":interrupt:" + effect.cast_id, effect.spell_id),
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
                    kind=RuntimeSpellEvent(
                        effect=effect.model_copy(update={"phase": "ended"}),
                        result=SpellResult(outcome="cancelled"),
                    ).model_dump_json(),
                )
            )
    return state.model_copy(update={"events": state.events + tuple(events)})
