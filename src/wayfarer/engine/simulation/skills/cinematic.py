"""Authoritative, replay-safe execution for bounded cinematic skill attempts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import Modifier, ModifierKind, RandomSource, success_check
from wayfarer.engine.rules.skills.cinematic import BINDING_BY_ID, PACKAGE_ID
from wayfarer.engine.simulation.resources import Pool, Receipt, ResourceEvent, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Record

VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Procedure:
    fatigue: int = 0
    focus: bool = False


PROCEDURES = {
    binding.id: Procedure(
        fatigue={
            "skill:breaking-blow": 1,
            "skill:flying-leap": 1,
            "skill:power-blow": 1,
            "skill:persuade": 2,
            "skill:suggest": 6,
            "skill:captivate": 8,
            "skill:sway-emotions": 4,
        }.get(binding.id, 0),
        focus=binding.id
        in {
            "skill:breaking-blow",
            "skill:flying-leap",
            "skill:power-blow",
            "skill:zen-archery",
        },
    )
    for binding in BINDING_BY_ID.values()
}


class CinematicSkillCommand(Record):
    id: str
    actor_id: str
    expected_revision: int = Field(ge=0)
    build_revision: str
    skill_id: str
    target_actor_id: str | None = None
    concentration_turns: int = Field(default=0, ge=0, le=32)
    interrupted: bool = False


class CinematicSkillOutcome(Record):
    command_id: str
    actor_id: str
    skill_id: str
    target_actor_id: str | None = None
    outcome: Literal["critical-success", "success", "failure", "critical-failure"]
    margin: int
    fatigue_spent: int = Field(ge=0)


def _digest(command: CinematicSkillCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _event(command_id: str) -> str:
    return "cinematic-skill:" + hashlib.sha256(command_id.encode()).hexdigest()


def _history(state: ResourceState) -> tuple[CinematicSkillOutcome, ...]:
    return tuple(
        CinematicSkillOutcome.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith("cinematic-skill:")
    )


def visible_history(
    state: ResourceState, *, viewer_actor_id: str, gm: bool = False
) -> tuple[CinematicSkillOutcome, ...]:
    """Keep secret cinematic checks visible only to their actor and the GM."""
    return tuple(value for value in _history(state) if gm or value.actor_id == viewer_actor_id)


def _time_modifier(turns: int) -> int:
    if turns >= 32:
        return 0
    if turns >= 16:
        return -1
    if turns >= 8:
        return -2
    if turns >= 4:
        return -3
    if turns >= 2:
        return -4
    if turns >= 1:
        return -5
    return -10


def apply_cinematic_skill(
    state: ResourceState,
    world: World,
    build: ValidatedBuild,
    command: CinematicSkillCommand,
    *,
    authorized_actor_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, CinematicSkillOutcome]:
    """Commit one catalog-owned attempt under CAS and the shared receipt ledger."""
    digest = _digest(command)
    receipt = next((value for value in state.receipts if value.command_id == command.id), None)
    if receipt is not None:
        if receipt.digest != digest:
            raise ConflictError("Cinematic command ID was reused with different intent")
        outcome = next((value for value in _history(state) if value.command_id == command.id), None)
        if outcome is None:
            raise ValidationError("Cinematic receipt has no matching outcome")
        return state, outcome
    if command.expected_revision != state.revision:
        raise ConflictError("Cinematic skill revision conflict")
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Cinematic skill actor lacks authority")
    if command.build_revision != build.revision:
        raise ValidationError("Cinematic skill build approval changed")
    binding = BINDING_BY_ID.get(command.skill_id)
    procedure = PROCEDURES.get(command.skill_id)
    if binding is None or procedure is None:
        raise ValidationError("Unsupported cinematic skill")
    if command.interrupted:
        raise ConflictError("Cinematic skill concentration was interrupted")
    if not procedure.focus and command.concentration_turns:
        raise ValidationError("This cinematic skill has no concentration schedule")
    entities = {entity.id: entity for entity in world.entities}
    actor = entities.get(command.actor_id)
    target = entities.get(command.target_actor_id) if command.target_actor_id else None
    if actor is None or actor.kind is not EntityKind.ACTOR:
        raise ValidationError("Cinematic skill actor is unavailable")
    if command.target_actor_id is not None and (
        target is None
        or target.kind is not EntityKind.ACTOR
        or target.location_id != actor.location_id
    ):
        raise ValidationError("Cinematic skill target is unavailable")
    levels = {value.target: int(value.value) for value in build.sheet.values}
    level = levels.get(command.skill_id)
    if level is None:
        raise ValidationError("Approved build does not know this cinematic skill")
    modifiers = (
        (
            Modifier(
                _time_modifier(command.concentration_turns),
                "cinematic concentration",
                command.skill_id,
                VERSION,
                ModifierKind.TIME,
            ),
        )
        if procedure.focus
        else ()
    )
    trace = success_check(
        level,
        modifiers,
        rng=rng,
        rules_package=PACKAGE_ID,
        rules_version=VERSION,
    )
    pools = list(state.pools)
    if procedure.fatigue:
        index = next(
            (i for i, pool in enumerate(pools) if pool.id == "fp:" + command.actor_id), None
        )
        if index is None or pools[index].current < procedure.fatigue:
            raise ValidationError("Cinematic skill requires sufficient FP")
        pool = pools[index]
        pools[index] = Pool(
            id=pool.id,
            current=pool.current - procedure.fatigue,
            maximum=pool.maximum,
            injury=pool.injury,
            fatigue=pool.fatigue,
        )
    outcome = CinematicSkillOutcome(
        command_id=command.id,
        actor_id=command.actor_id,
        skill_id=command.skill_id,
        target_actor_id=command.target_actor_id,
        outcome=trace.outcome.value,
        margin=trace.margin,
        fatigue_spent=procedure.fatigue,
    )
    return (
        state.model_copy(
            update={
                "revision": state.revision + 1,
                "pools": tuple(pools),
                "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
                "events": state.events
                + (
                    ResourceEvent(
                        id=_event(command.id),
                        at=state.game_time,
                        kind=outcome.model_dump_json(),
                        target_id=command.actor_id,
                    ),
                ),
            }
        ),
        outcome,
    )
