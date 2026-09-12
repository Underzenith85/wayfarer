"""Persisted activation protocol for switchable movement and body forms."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.movement_forms import MovementForms, movement_forms
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

SWITCHABLE = frozenset(
    {
        "advantage:alternate-form",
        "advantage:growth",
        "advantage:insubstantiality",
        "advantage:morph",
        "advantage:shadow-form",
        "advantage:shrinking",
    }
)
PREFIX = "movement-form:"


class MovementFormCommand(Command):
    kind: Literal["start", "resolve", "cancel", "interrupt"]
    definition_id: str


class MovementFormEffect(Record):
    actor_id: str
    definition_id: str
    build_revision: str
    started_at: int = Field(ge=0)
    ready_at: int = Field(ge=0)
    concentrating: bool = False
    active: bool = False


class MovementFormOutcome(Record):
    outcome: Literal["concentrating", "active", "cancelled", "interrupted"]
    effect: MovementFormEffect


class MovementFormEvent(Record):
    command_id: str
    kind: Literal["start", "resolve", "cancel", "interrupt"]
    outcome: MovementFormOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[MovementFormEvent, ...]:
    return tuple(
        MovementFormEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def active_forms(resources: ResourceState) -> tuple[MovementFormEffect, ...]:
    latest: dict[tuple[str, str], MovementFormEffect] = {}
    for event in history(resources):
        effect = event.outcome.effect
        latest[effect.actor_id, effect.definition_id] = effect
    return tuple(effect for effect in latest.values() if effect.active or effect.concentrating)


def visible_forms(
    resources: ResourceState, world: World, observer_id: str
) -> tuple[MovementFormEffect, ...]:
    visible = {entity.id for entity in world.perspective(observer_id).entities}
    return tuple(effect for effect in active_forms(resources) if effect.actor_id in visible)


def apply_movement_form(
    resources: ResourceState,
    world: World,
    command: MovementFormCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, MovementFormOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Movement/form execution requires actor authority")
    prior = next((event for event in history(resources) if event.command_id == command.id), None)
    if prior is not None:
        effect = prior.outcome.effect
        if (
            prior.kind == command.kind
            and effect.actor_id == command.actor_id
            and effect.definition_id == command.definition_id
        ):
            return resources, prior.outcome
        raise ConflictError("Movement/form command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Movement/form revision changed")
    if command.definition_id not in SWITCHABLE:
        raise ValidationError("Trait is not a switchable form")
    entities = {entity.id for entity in world.entities}
    if command.actor_id not in entities:
        raise ValidationError("Movement/form actor is unavailable")
    approved: MovementForms = movement_forms(build, definitions)
    purchase = approved.purchase(command.definition_id)
    if purchase is None:
        raise ValidationError("Movement/form trait is not in the approved build")
    if build.revision != next(
        (
            entry.build_revision
            for entry in active_forms(resources)
            if entry.actor_id == command.actor_id and entry.definition_id == command.definition_id
        ),
        build.revision,
    ):
        raise ValidationError("Active form belongs to an obsolete build")
    old = next(
        (
            entry
            for entry in active_forms(resources)
            if entry.actor_id == command.actor_id and entry.definition_id == command.definition_id
        ),
        None,
    )
    if command.kind == "start":
        if old is not None:
            raise ValidationError("Movement/form trait is already active")
        delay = (
            10 if command.definition_id in {"advantage:alternate-form", "advantage:morph"} else 1
        )
        effect = MovementFormEffect(
            actor_id=command.actor_id,
            definition_id=command.definition_id,
            build_revision=build.revision,
            started_at=resources.game_time,
            ready_at=resources.game_time + delay,
            concentrating=True,
        )
        outcome = MovementFormOutcome(outcome="concentrating", effect=effect)
    elif command.kind == "resolve":
        if old is None or not old.concentrating or resources.game_time < old.ready_at:
            raise ValidationError("Movement/form change is not ready")
        effect = old.model_copy(update={"concentrating": False, "active": True})
        outcome = MovementFormOutcome(outcome="active", effect=effect)
    elif command.kind == "cancel":
        if old is None or not old.active:
            raise ValidationError("Movement/form trait is not active")
        effect = old.model_copy(update={"active": False})
        outcome = MovementFormOutcome(outcome="cancelled", effect=effect)
    else:
        if old is None or not old.concentrating:
            raise ValidationError("No movement/form change is being concentrated on")
        effect = old.model_copy(update={"concentrating": False})
        outcome = MovementFormOutcome(outcome="interrupted", effect=effect)
    event = MovementFormEvent(command_id=command.id, kind=command.kind, outcome=outcome)
    return (
        resources.model_copy(
            update={
                "revision": resources.revision + 1,
                "events": resources.events
                + (
                    ResourceEvent(
                        id=_event_id(command.id),
                        at=resources.game_time,
                        target_id=command.actor_id,
                        kind=event.model_dump_json(),
                    ),
                ),
            }
        ),
        outcome,
    )
