"""Core Growth/Shrinking body state; approved traits authorize timed size changes."""

from __future__ import annotations

import hashlib
from fractions import Fraction
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.tables.size_forms import (
    dimension_yards,
    growth_minimum_st,
    height_ratio,
    reduced_result,
    shrinking_weight_ratio,
)
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState, is_carried
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "size-form:"


class SizeFormCommand(Command):
    kind: Literal["start", "interrupt"]
    target_delta: int = Field(default=0, ge=-100, le=100)


class SizeFormEffect(Record):
    actor_id: str
    build_revision: str
    native_sm: int
    native_hp: int = Field(ge=1)
    current_delta: int
    target_delta: int
    started_at: int = Field(ge=0)
    changing: bool
    in_combat: bool = False
    ready_credit: int = Field(default=0, ge=0, le=1)
    hp_current: int
    hp_maximum: int = Field(ge=1)
    lost_hp_fraction: Fraction


class SizeFormEvent(Record):
    command: SizeFormCommand | None = None
    effect: SizeFormEffect


def effects(resources: ResourceState) -> tuple[SizeFormEffect, ...]:
    latest: dict[str, SizeFormEffect] = {}
    for event in resources.events:
        if event.id.startswith(PREFIX):
            effect = SizeFormEvent.model_validate_json(event.kind).effect
            latest[effect.actor_id] = effect
    return tuple(latest.values())


def visible_effects(
    resources: ResourceState, world: World, observer_id: str
) -> tuple[SizeFormEffect, ...]:
    visible = {e.id for e in world.perspective(observer_id).entities}
    return tuple(e for e in effects(resources) if e.actor_id in visible)


def effect_for(resources: ResourceState, actor_id: str) -> SizeFormEffect | None:
    return next((e for e in effects(resources) if e.actor_id == actor_id), None)


def size_delta(resources: ResourceState, actor_id: str) -> int:
    effect = effect_for(resources, actor_id)
    return effect.current_delta if effect else 0


def reduced_body_result(resources: ResourceState, actor_id: str, value: int) -> int:
    effect = effect_for(resources, actor_id)
    if effect is None or effect.current_delta >= 0:
        return value
    return reduced_result(value, height_ratio(effect.native_sm, effect.current_delta))


def _append(resources: ResourceState, identifier: str, event: SizeFormEvent) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=identifier,
                    at=resources.game_time,
                    target_id=event.effect.actor_id,
                    kind=event.model_dump_json(),
                ),
            )
        }
    )


def _check_target(build: ValidatedBuild, native_sm: int, delta: int) -> None:
    if delta > 0 and native_sm + delta > 10:
        raise ValidationError(
            "Growth above the verified B402 reach table requires a separate protocol"
        )
    identifier = "advantage:growth" if delta > 0 else "advantage:shrinking"
    purchase = next((p for p in build.trait_purchases if p.definition_id == identifier), None)
    if purchase is None or abs(delta) > purchase.amount:
        raise ValidationError("Size change exceeds the approved purchased levels")
    if purchase.trait is not None and purchase.trait.modifiers:
        raise ValidationError("Size-form modifier requires a separately verified protocol")
    assert build.statistics is not None
    if delta > 0 and build.statistics.st < growth_minimum_st(native_sm, purchase.amount):
        raise ValidationError("Growth requires purchased ST sufficient for maximum height")


def apply_size_form(
    resources: ResourceState,
    command: SizeFormCommand,
    build: ValidatedBuild,
    *,
    authorized_actor_id: str,
    system: bool = False,
    in_combat: bool = False,
) -> tuple[ResourceState, SizeFormEffect]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Size change requires actor authority")
    identifier = PREFIX + hashlib.sha256(command.id.encode()).hexdigest()
    prior = next((e for e in resources.events if e.id == identifier), None)
    if prior is not None:
        event = SizeFormEvent.model_validate_json(prior.kind)
        if event.command != command:
            raise ConflictError("Size command ID was already used")
        return resources, event.effect
    if resources.revision != command.expected_revision:
        raise ConflictError("Size change revision changed")
    if build.statistics is None:
        raise ValidationError("Size changes require approved character statistics")
    old = effect_for(resources, command.actor_id)
    if old is not None and old.build_revision != build.revision:
        if old.current_delta or old.changing:
            raise ValidationError("Size state belongs to an obsolete approved build")
        old = None
    if command.kind == "interrupt" and (old is None or not old.changing):
        raise ValidationError("No size change is in progress")
    delta = old.current_delta if old else 0
    native_sm = (
        old.native_sm
        if old
        else next(
            (p.amount for p in build.purchases if p.definition_id == "trait:size-modifier"), 0
        )
    )
    if command.kind == "start":
        if old is not None and old.changing:
            raise ValidationError("Size change is already in progress")
        if command.target_delta == delta:
            raise ValidationError("Character is already at the requested size")
        if command.target_delta:
            _check_target(build, native_sm, command.target_delta)
        if delta and command.target_delta and (delta > 0) != (command.target_delta > 0):
            raise ValidationError("Return to native size before switching size traits")
        target_hp = (
            reduced_result(build.statistics.hp, height_ratio(native_sm, command.target_delta))
            if command.target_delta < 0
            else build.statistics.hp
        )
        if target_hp < 1:
            raise ValidationError("Sub-one-HP forms require a fractional-health protocol")
        if command.target_delta < 0 and any(
            i.owner_id == command.actor_id and is_carried(resources, i) for i in resources.items
        ):
            raise ValidationError(
                "Core Shrinking requires leaving all equipment at its actual location"
            )
    hp = next(p for p in resources.pools if p.id == "hp:" + command.actor_id)
    effect = SizeFormEffect(
        actor_id=command.actor_id,
        build_revision=build.revision,
        native_sm=native_sm,
        native_hp=build.statistics.hp,
        current_delta=delta,
        target_delta=command.target_delta if command.kind == "start" else delta,
        started_at=resources.game_time,
        changing=command.kind == "start",
        in_combat=in_combat,
        hp_current=hp.current,
        hp_maximum=hp.maximum,
        lost_hp_fraction=(
            max(
                Fraction(0),
                old.lost_hp_fraction + Fraction(old.hp_current - hp.current, old.hp_maximum),
            )
            if old
            else Fraction(hp.maximum - hp.current, hp.maximum)
        ),
    )
    updated = _append(resources, identifier, SizeFormEvent(command=command, effect=effect))
    return updated.model_copy(update={"revision": resources.revision + 1}), effect


def checkpoint(resources: ResourceState) -> ResourceState:
    """Settle each elapsed second's size before subsequent resource/health work."""
    for effect in effects(resources):
        if not effect.changing or effect.started_at == resources.game_time:
            continue
        elapsed = resources.game_time - effect.started_at
        distance = min(elapsed, abs(effect.target_delta - effect.current_delta))
        if effect.in_combat:
            distance = min(distance, effect.ready_credit)
        if not distance:
            continue
        direction = 1 if effect.target_delta > effect.current_delta else -1
        delta = effect.current_delta + direction * distance
        hp = next(p for p in resources.pools if p.id == "hp:" + effect.actor_id)
        loss = max(
            Fraction(0),
            effect.lost_hp_fraction + Fraction(effect.hp_current - hp.current, effect.hp_maximum),
        )
        maximum = (
            reduced_result(effect.native_hp, height_ratio(effect.native_sm, delta))
            if delta < 0
            else effect.native_hp
        )
        current = maximum * (1 - loss)
        current_int = current.numerator // current.denominator
        changed_hp = hp.model_copy(update={"maximum": maximum, "current": current_int})
        resources = resources.model_copy(
            update={"pools": tuple(changed_hp if p.id == hp.id else p for p in resources.pools)}
        )
        changed = effect.model_copy(
            update={
                "current_delta": delta,
                "started_at": resources.game_time,
                "changing": delta != effect.target_delta,
                "hp_current": current_int,
                "hp_maximum": maximum,
                "lost_hp_fraction": loss,
                "ready_credit": 0,
            }
        )
        resources = _append(
            resources,
            PREFIX + "tick:" + effect.actor_id + ":" + str(resources.game_time),
            SizeFormEvent(effect=changed),
        )
    return resources


def ready_step(resources: ResourceState, actor_id: str, command_id: str) -> ResourceState:
    """A combat Ready permits one SM change in the next elapsed second."""
    effect = effect_for(resources, actor_id)
    if effect is None or not effect.changing or not effect.in_combat:
        raise ValidationError("No combat body-size change is being readied")
    identifier = PREFIX + "ready:" + hashlib.sha256(command_id.encode()).hexdigest()
    if any(e.id == identifier for e in resources.events):
        return resources
    changed = effect.model_copy(update={"ready_credit": 1, "started_at": resources.game_time})
    return _append(resources, identifier, SizeFormEvent(effect=changed))


class BodySizeGeometry(Record):
    size_modifier: int
    table_dimension_yards: Fraction
    height_fraction: Fraction
    weight_fraction: Fraction | None


def body_geometry(
    resources: ResourceState, actor_id: str, *, native_sm: int = 0
) -> BodySizeGeometry:
    """Actual committed body geometry; weight scaling is only specified for Shrinking."""
    effect = effect_for(resources, actor_id)
    native = effect.native_sm if effect else native_sm
    delta = effect.current_delta if effect else 0
    return BodySizeGeometry(
        size_modifier=native + delta,
        table_dimension_yards=dimension_yards(native + delta),
        height_fraction=height_ratio(native, delta),
        weight_fraction=shrinking_weight_ratio(-delta)
        if delta < 0
        else Fraction(1)
        if delta == 0
        else None,
    )


def require_native_size(resources: ResourceState, actor_id: str) -> None:
    effect = effect_for(resources, actor_id)
    if effect is not None and (effect.current_delta or effect.changing):
        raise ValidationError("Return to native size before changing the approved body")
