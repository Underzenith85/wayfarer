"""Authoritative, replay-safe execution for bounded cinematic skill attempts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import (
    Modifier,
    ModifierKind,
    Outcome,
    RandomSource,
    success_check,
)
from wayfarer.engine.rules.skills.cinematic import BINDING_BY_ID, PACKAGE_ID
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import ContinueExertion, FatigueCost, apply_fatigue
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.simulation.skills.physiology import PhysiologyAdjustment
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
    physiology: PhysiologyAdjustment | None = Field(default=None, exclude_if=lambda v: v is None)
    exertion_allowed: bool = Field(default=True, exclude_if=lambda v: v)


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


def _physiology_modifiers(
    command: CinematicSkillCommand, physiology: PhysiologyAdjustment | None
) -> tuple[Modifier, ...]:
    physiology_skill = command.skill_id in {"skill:pressure-points", "skill:pressure-secrets"}
    if physiology_skill and (
        physiology is None
        or physiology.actor_id != command.actor_id
        or physiology.target_actor_id != command.target_actor_id
    ):
        raise ValidationError("Pressure-point skills require authoritative species context")
    if not physiology_skill and physiology is not None:
        raise ValidationError("Physiology context does not apply to this cinematic skill")
    return () if physiology is None else physiology.modifier()


def apply_cinematic_skill(
    state: ResourceState,
    world: World,
    build: ValidatedBuild,
    command: CinematicSkillCommand,
    *,
    authorized_actor_id: str,
    rng: RandomSource,
    physiology: PhysiologyAdjustment | None = None,
    skill_modifiers: tuple[Modifier, ...] = (),
) -> tuple[ResourceState, CinematicSkillOutcome]:
    """Commit one catalog-owned attempt under CAS and the shared receipt ledger."""
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Cinematic skill actor lacks authority")
    if command.build_revision != build.revision:
        raise ValidationError("Cinematic skill build approval changed")
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
    physiology_modifiers = _physiology_modifiers(command, physiology)
    levels = {value.target: int(value.value) for value in build.sheet.values}
    level = levels.get(command.skill_id)
    if level is None:
        raise ValidationError("Approved build does not know this cinematic skill")
    modifiers = (
        (
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
        + physiology_modifiers
        + check_modifiers(state, command.actor_id, binding.attribute.value.split(":")[-1])
        + skill_modifiers
    )
    allowed = True
    if procedure.fatigue:
        state, exertion = apply_fatigue(
            state,
            ContinueExertion(
                id="cinematic-exertion:" + command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
            ),
            ht=levels.get("attribute:ht", 0),
            will=levels.get("secondary:will", 0),
            rng=rng,
            system=True,
        )
        allowed = exertion.allowed
    trace = (
        success_check(level, modifiers, rng=rng, rules_package=PACKAGE_ID, rules_version=VERSION)
        if allowed
        else None
    )
    spent = 0
    if procedure.fatigue and allowed:
        state, cost = apply_fatigue(
            state,
            FatigueCost(
                id="cinematic-cost:" + command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                amount=procedure.fatigue,
                power=True,
            ),
            ht=levels.get("attribute:ht", 0),
            rng=rng,
            system=True,
        )
        spent = cost.fp_lost
    outcome = CinematicSkillOutcome(
        command_id=command.id,
        actor_id=command.actor_id,
        skill_id=command.skill_id,
        target_actor_id=command.target_actor_id,
        outcome=trace.outcome.value if trace else Outcome.FAILURE.value,
        margin=trace.margin if trace else 0,
        fatigue_spent=spent,
        exertion_allowed=allowed,
        physiology=physiology,
    )
    return (
        state.model_copy(
            update={
                "revision": command.expected_revision + 1,
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
